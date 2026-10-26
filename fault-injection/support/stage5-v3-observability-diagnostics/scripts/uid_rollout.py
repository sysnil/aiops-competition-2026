"""Guarded, reversible Downward API identity repair for four demo services."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import time


ROOT = Path("/root/stage5-v3-observability-diagnostics")
SERVICES = ("cart", "checkout", "shipping", "quote")
IDENTITY = "k8s.pod.uid=$(WG_POD_UID),k8s.pod.name=$(WG_POD_NAME)"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    if path.exists():
        raise ValueError("Refusing to overwrite " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def kube(*args, timeout=60):
    result = subprocess.run(["kubectl", "-n", "otel-demo", *args],
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("kubectl failed: " + result.stderr[-600:])
    return result.stdout


def deployment(service):
    return json.loads(kube("get", "deployment", service, "-o", "json"))


def env_of(value):
    containers = value["spec"]["template"]["spec"]["containers"]
    if len(containers) != 1 or containers[0]["name"] not in SERVICES:
        raise ValueError("Expected one named application container")
    return containers[0]["env"]


def enriched(original):
    names = [row["name"] for row in original]
    if (len(names) != len(set(names)) or "WG_POD_UID" in names or
            "WG_POD_NAME" in names or names.count("OTEL_RESOURCE_ATTRIBUTES") != 1):
        raise ValueError("Unexpected environment variables")
    updated = []
    for row in original:
        if row["name"] == "OTEL_RESOURCE_ATTRIBUTES":
            value = row.get("value", "")
            if "k8s.pod.uid" in value or "k8s.pod.name" in value:
                raise ValueError("Resource identity already exists")
            updated.append({**row, "value": value + "," + IDENTITY})
        else:
            updated.append(row)
    return [{"name": "WG_POD_UID", "valueFrom": {"fieldRef": {
                "apiVersion": "v1", "fieldPath": "metadata.uid"}}},
            {"name": "WG_POD_NAME", "valueFrom": {"fieldRef": {
                "apiVersion": "v1", "fieldPath": "metadata.name"}}}, *updated]


def ready_pods(service, expected_env=None):
    pods = json.loads(kube("get", "pods", "-l",
                           "opentelemetry.io/name=" + service, "-o", "json"))["items"]
    return [pod for pod in pods if pod["metadata"].get("deletionTimestamp") is None
            and pod["status"].get("phase") == "Running"
            and pod["status"].get("containerStatuses")
            and all(row.get("ready") for row in pod["status"]["containerStatuses"])
            and (expected_env is None or pod["spec"]["containers"][0].get(
                "env") == expected_env)]


def cluster_ready():
    nodes = json.loads(subprocess.check_output(["kubectl", "get", "nodes",
        "-o", "json"], text=True, timeout=20))["items"]
    if len(nodes) != 4:
        raise RuntimeError("Four-node precheck failed")
    for node in nodes:
        conditions = {item["type"]: item["status"] for item in node[
            "status"]["conditions"]}
        if conditions.get("Ready") != "True" or any(conditions.get(
                kind) != "False" for kind in ("DiskPressure", "MemoryPressure",
                                                "PIDPressure")):
            raise RuntimeError("Node safety condition failed")


def backup(path):
    if path.exists():
        raise ValueError("Backup directory already exists")
    path.mkdir(parents=True)
    cluster_ready()
    records = {}
    for service in SERVICES:
        live = deployment(service)
        pods = ready_pods(service)
        if live["spec"].get("replicas") != 1 or len(pods) != 1 or live[
                "status"].get("readyReplicas") != 1:
            raise RuntimeError(service + " is not one Ready replica")
        save(path / ("deployment-" + service + ".json"), live)
        save(path / ("pods-" + service + ".json"), json.loads(kube(
            "get", "pods", "-l", "opentelemetry.io/name=" + service,
            "-o", "json")))
        save(path / ("endpoints-" + service + ".json"), json.loads(kube(
            "get", "endpointslices", "-l", "kubernetes.io/service-name=" + service,
            "-o", "json")))
        records[service] = {"deployment_uid": live["metadata"]["uid"],
            "deployment_sha256": sha(path / ("deployment-" + service + ".json")),
            "old_pod_uid": pods[0]["metadata"]["uid"],
            "old_node": pods[0]["spec"].get("nodeName")}
    save(path / "otel-collector-config.json", json.loads(kube(
        "get", "configmap", "otel-collector", "-o", "json")))
    save(path / "backup-manifest.json", {"status": "IMMUTABLE_PRE_REPAIR_BACKUP",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "services": records,
        "collector_config_sha256": sha(path / "otel-collector-config.json")})
    return records


def plan(backup_root, output):
    records = json.loads((backup_root / "backup-manifest.json").read_text())[
        "services"]
    entries = {}
    for service in SERVICES:
        original_path = backup_root / ("deployment-" + service + ".json")
        original = json.loads(original_path.read_text())
        live = deployment(service)
        if (sha(original_path) != records[service]["deployment_sha256"] or
                live["metadata"]["uid"] != original["metadata"]["uid"] or
                env_of(live) != env_of(original) or live["spec"]["replicas"] != 1):
            raise ValueError(service + " changed after backup")
        entries[service] = {"deployment_uid": live["metadata"]["uid"],
            "original_env": env_of(original),
            "new_env": enriched(env_of(original)),
            "backup_sha256": sha(original_path),
            "old_pod_uid": records[service]["old_pod_uid"],
            "old_node": records[service]["old_node"]}
    save(output, {"status": "UID_REPAIR_PLAN_NOT_APPLIED",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "Downward_API_OTEL_RESOURCE_ATTRIBUTES",
        "services": entries, "fault_injected": False,
        "stage5_v3_protocol_frozen": False})
    return entries


def patch(service, expected, target, deploy_uid, allow_unready_current=False):
    cluster_ready()
    live = deployment(service)
    if (live["metadata"]["uid"] != deploy_uid or env_of(live) != expected or
            live["spec"].get("replicas") != 1 or (not allow_unready_current and
            live["status"].get("readyReplicas") != 1)):
        raise ValueError(service + " live state differs from registered plan")
    operations = [{"op": "test", "path": "/metadata/resourceVersion",
                   "value": live["metadata"]["resourceVersion"]},
                  {"op": "replace", "path": "/spec/template/spec/containers/0/env",
                   "value": target}]
    kube("patch", "deployment", service, "--type=json", "-p",
         json.dumps(operations), timeout=45)
    kube("rollout", "status", "deployment/" + service,
         "--timeout=240s", timeout=260)
    updated = deployment(service)
    if (env_of(updated) != target or updated["status"].get("readyReplicas") != 1
            or updated["status"].get("updatedReplicas") != 1):
        raise RuntimeError(service + " rollout not fully Ready")
    for _ in range(20):
        pods = ready_pods(service, target)
        if len(pods) == 1:
            break
        time.sleep(2)
    else:
        raise RuntimeError(service + " new Pod not uniquely Ready")
    pod = pods[0]
    try:
        rendered = kube("exec", pod["metadata"]["name"], "--", "printenv",
                        "OTEL_RESOURCE_ATTRIBUTES").strip()
    except RuntimeError:
        rendered = None
    target_resource = next(row["value"] for row in target if row[
        "name"] == "OTEL_RESOURCE_ATTRIBUTES")
    if rendered is not None and "k8s.pod.uid" in target_resource:
        if ("k8s.pod.uid=" + pod["metadata"]["uid"] not in rendered or
                "k8s.pod.name=" + pod["metadata"]["name"] not in rendered):
            raise RuntimeError(service + " expanded Pod identity differs from Kubernetes")
    elif rendered is not None and rendered != target_resource:
        raise RuntimeError(service + " rollback resource attributes differ")
    return {"service": service, "pod_name": pod["metadata"]["name"],
        "pod_uid": pod["metadata"]["uid"], "node": pod["spec"].get("nodeName"),
        "deployment_resource_version": updated["metadata"]["resourceVersion"],
        "rendered_resource_sha256": hashlib.sha256(rendered.encode()).hexdigest()
            if rendered is not None else None,
        "runtime_env_check": "MATCHED_POD_IDENTITY" if rendered is not None
            else "UNAVAILABLE_NO_CONTAINER_EXEC_VERIFIED_LATER_BY_TRACE",
        "at_utc": datetime.now(timezone.utc).isoformat()}


def apply_one(service, plan_path, output):
    entries = json.loads(plan_path.read_text())["services"]
    entry = entries[service]
    with (ROOT / "runtime/uid-rollout.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            result = patch(service, entry["original_env"], entry["new_env"],
                           entry["deployment_uid"])
            result["status"] = "UID_REPAIR_ROLLOUT_PASS"
        except Exception as exc:
            result = {"service": service, "status": "UID_REPAIR_ROLLOUT_FAILED",
                      "error": str(exc)}
            live = deployment(service)
            if env_of(live) == entry["new_env"]:
                try:
                    result["automatic_rollback"] = patch(service,
                        entry["new_env"], entry["original_env"],
                        entry["deployment_uid"], allow_unready_current=True)
                except Exception as rollback_error:
                    result["automatic_rollback_error"] = str(rollback_error)
            save(output, result)
            raise
        save(output, result)
        return result


def rollback_all(plan_path, output):
    entries = json.loads(plan_path.read_text())["services"]
    actions = []
    with (ROOT / "runtime/uid-rollout.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for service in reversed(SERVICES):
            entry = entries[service]
            live = deployment(service)
            if live["metadata"]["uid"] != entry["deployment_uid"]:
                raise ValueError(service + " deployment UID changed")
            if env_of(live) == entry["original_env"]:
                actions.append({"service": service, "status": "ALREADY_ORIGINAL"})
            elif env_of(live) == entry["new_env"]:
                actions.append({"service": service,
                    "status": "ROLLED_BACK", "result": patch(service,
                        entry["new_env"], entry["original_env"],
                        entry["deployment_uid"], allow_unready_current=True)})
            else:
                raise ValueError(service + " has an unknown intervening environment")
    save(output, {"status": "UID_REPAIR_ROLLBACK_COMPLETE",
                  "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                  "actions": actions})
    return actions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("backup", "plan", "apply", "rollback-all"))
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--service", choices=SERVICES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode == "backup":
        if not args.backup_root:
            raise ValueError("Backup path required")
        value = backup(args.backup_root)
    elif args.mode == "plan":
        if not args.backup_root or not args.output:
            raise ValueError("Backup root and plan output required")
        value = plan(args.backup_root, args.output)
    elif args.mode == "apply":
        if not args.service or not args.plan or not args.output:
            raise ValueError("Service, plan and output required")
        value = apply_one(args.service, args.plan, args.output)
    else:
        if not args.plan or not args.output:
            raise ValueError("Plan and output required")
        value = rollback_all(args.plan, args.output)
    print(json.dumps({"mode": args.mode, "status": "PASS", "detail": value}))


if __name__ == "__main__":
    main()
