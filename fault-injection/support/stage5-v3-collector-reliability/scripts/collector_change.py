#!/usr/bin/env python3
"""One guarded Collector soft-limit candidate with independent rollback."""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import yaml


ROOT = Path("/root/stage5-v3-collector-reliability")
ANNOTATION = "stage5v3-reliability"
TIMER = "s5v3cr-collector-rollback.timer"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    if path.exists():
        raise ValueError("Refusing overwrite: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def read(path):
    return json.loads(path.read_text())


def kube(*args, timeout=35):
    result = subprocess.run(["kubectl", "-n", "otel-demo", *args],
        capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("kubectl " + " ".join(args[:3]) + ": " + result.stderr[-500:])
    return result.stdout


def object_json(kind, name):
    return json.loads(kube("get", kind, name, "-o", "json"))


def gateway_pod():
    pods = json.loads(kube("get", "pods", "-o", "json"))["items"]
    rows = [pod for pod in pods if pod["metadata"]["name"].startswith("otel-collector-")
        and pod["metadata"].get("deletionTimestamp") is None and
        pod["status"].get("phase") == "Running" and
        pod["status"].get("containerStatuses") and
        all(row.get("ready") for row in pod["status"]["containerStatuses"])]
    if len(rows) != 1:
        raise RuntimeError("Gateway Collector is not one Ready Pod")
    return rows[0]


def backup(directory):
    if directory.exists():
        raise ValueError("Refusing backup overwrite")
    directory.mkdir(parents=True)
    objects = {
        "configmap": object_json("configmap", "otel-collector"),
        "deployment": object_json("deployment", "otel-collector"),
        "pod": gateway_pod(),
        "service": object_json("service", "otel-collector"),
    }
    for name, value in objects.items():
        save(directory / (name + ".json"), value)
    manifest = {"status": "COLLECTOR_ORIGINAL_CONFIG_BACKED_UP",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "sha256": {name: sha(directory / (name + ".json")) for name in objects},
        "pod_uid": objects["pod"]["metadata"]["uid"],
        "runtime_image_id": objects["pod"]["status"]["containerStatuses"][0].get("imageID"),
        "deployment_uid": objects["deployment"]["metadata"]["uid"],
        "configmap_uid": objects["configmap"]["metadata"]["uid"]}
    save(directory / "manifest.json", manifest)
    return manifest


def plan(directory, prereg_path, output):
    prereg = yaml.safe_load(prereg_path.read_text())
    if (prereg["status"] != "PREREGISTERED_BEFORE_COLLECTOR_CHANGE" or
            prereg["single_candidate"]["old"] != 25 or
            prereg["single_candidate"]["new"] != 15):
        raise ValueError("Unregistered candidate")
    original = read(directory / "configmap.json")
    deployment = read(directory / "deployment.json")
    pod = read(directory / "pod.json")
    manifest = read(directory / "manifest.json")
    if (sha(directory / "configmap.json") != manifest["sha256"]["configmap"] or
            sha(directory / "deployment.json") != manifest["sha256"]["deployment"]):
        raise ValueError("Backup hash changed")
    relay = original["data"]["relay"]
    old_config = yaml.safe_load(relay)
    limiter = old_config["processors"]["memory_limiter"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {row["name"]: row.get("value") for row in container["env"]}
    annotations = deployment["spec"]["template"]["metadata"].get("annotations", {})
    if (limiter != {"check_interval": "5s", "limit_percentage": 80,
                    "spike_limit_percentage": 25} or
            container["resources"]["limits"]["memory"] != "400Mi" or
            env.get("GOMEMLIMIT") != "320MiB" or ANNOTATION in annotations or
            pod["metadata"]["uid"] != manifest["pod_uid"] or
            deployment["status"].get("readyReplicas") != 1):
        raise ValueError("Gateway configuration differs from studied candidate")
    candidate = copy.deepcopy(old_config)
    candidate["processors"]["memory_limiter"]["spike_limit_percentage"] = 15
    new_relay = yaml.safe_dump(candidate, sort_keys=False, allow_unicode=True)
    if yaml.safe_load(new_relay) != candidate:
        raise ValueError("Candidate YAML roundtrip changed semantics")
    value = {"status": "SINGLE_COLLECTOR_CANDIDATE_PLANNED_NOT_APPLIED",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "backup_dir": str(directory), "backup_manifest_sha256": sha(directory / "manifest.json"),
        "prereg_sha256": sha(prereg_path), "original_relay": relay, "new_relay": new_relay,
        "original_relay_sha256": hashlib.sha256(relay.encode()).hexdigest(),
        "new_relay_sha256": hashlib.sha256(new_relay.encode()).hexdigest(),
        "original_configmap_uid": original["metadata"]["uid"],
        "original_deployment_uid": deployment["metadata"]["uid"],
        "original_pod_uid": pod["metadata"]["uid"],
        "original_annotations": annotations,
        "annotation_key": ANNOTATION,
        "candidate_semantic_diff": {"processors.memory_limiter.spike_limit_percentage": [25, 15]},
        "container_limit_unchanged": "400Mi", "gomemlimit_unchanged": "320MiB",
        "fault_injected": False}
    save(output, value)
    return {"status": value["status"], "plan_sha256": sha(output),
            "new_relay_sha256": value["new_relay_sha256"]}


def timer_active():
    result = subprocess.run(["systemctl", "is-active", TIMER],
                            capture_output=True, text=True, timeout=10)
    return result.returncode == 0 and result.stdout.strip() == "active"


def patch_configmap(expected, target, uid):
    live = object_json("configmap", "otel-collector")
    if live["metadata"]["uid"] != uid or live["data"]["relay"] != expected:
        raise ValueError("Collector ConfigMap changed after registration")
    operations = [{"op": "test", "path": "/metadata/resourceVersion",
                   "value": live["metadata"]["resourceVersion"]},
                  {"op": "test", "path": "/data/relay", "value": expected},
                  {"op": "replace", "path": "/data/relay", "value": target}]
    kube("patch", "configmap", "otel-collector", "--type=json", "-p",
         json.dumps(operations), timeout=45)


def patch_annotation(plan_value, add):
    live = object_json("deployment", "otel-collector")
    annotations = live["spec"]["template"]["metadata"].get("annotations", {})
    expected_value = plan_value["new_relay_sha256"][:16]
    if live["metadata"]["uid"] != plan_value["original_deployment_uid"]:
        raise ValueError("Deployment identity changed")
    if add and ANNOTATION in annotations:
        raise ValueError("Candidate annotation already exists")
    if not add and annotations.get(ANNOTATION) != expected_value:
        raise ValueError("Candidate annotation differs before rollback")
    operations = [{"op": "test", "path": "/metadata/resourceVersion",
                   "value": live["metadata"]["resourceVersion"]}]
    if add:
        operations.append({"op": "add", "path": "/spec/template/metadata/annotations/" + ANNOTATION,
                           "value": expected_value})
    else:
        operations.append({"op": "remove", "path": "/spec/template/metadata/annotations/" + ANNOTATION})
    kube("patch", "deployment", "otel-collector", "--type=json", "-p",
         json.dumps(operations), timeout=45)
    kube("rollout", "status", "deployment/otel-collector", "--timeout=240s", timeout=260)


def verify_live(plan_value, candidate):
    live_cm = object_json("configmap", "otel-collector")
    live_deploy = object_json("deployment", "otel-collector")
    pod = gateway_pod()
    expected_relay = plan_value["new_relay"] if candidate else plan_value["original_relay"]
    expected_annotation = plan_value["new_relay_sha256"][:16] if candidate else None
    actual_annotation = live_deploy["spec"]["template"]["metadata"].get(
        "annotations", {}).get(ANNOTATION)
    if (live_cm["data"]["relay"] != expected_relay or
            actual_annotation != expected_annotation or
            live_deploy["status"].get("readyReplicas") != 1 or
            pod["status"]["containerStatuses"][0].get("restartCount") != 0 or
            (candidate and pod["metadata"]["uid"] == plan_value["original_pod_uid"])):
        raise RuntimeError("Collector rollout configuration or readiness mismatch")
    return {"pod_name": pod["metadata"]["name"], "pod_uid": pod["metadata"]["uid"],
            "pod_ip": pod["status"].get("podIP"), "node": pod["spec"].get("nodeName"),
            "configmap_resource_version": live_cm["metadata"]["resourceVersion"],
            "deployment_resource_version": live_deploy["metadata"]["resourceVersion"],
            "runtime_image_id": pod["status"]["containerStatuses"][0].get("imageID"),
            "configuration": "CANDIDATE" if candidate else "ORIGINAL"}


def rollback(plan_value, output):
    if output.exists():
        raise ValueError("Rollback record already exists")
    cm = object_json("configmap", "otel-collector")
    if cm["data"]["relay"] == plan_value["new_relay"]:
        patch_configmap(plan_value["new_relay"], plan_value["original_relay"],
                        plan_value["original_configmap_uid"])
    elif cm["data"]["relay"] != plan_value["original_relay"]:
        raise ValueError("Unknown ConfigMap state cannot be safely rolled back")
    deploy = object_json("deployment", "otel-collector")
    annotation = deploy["spec"]["template"]["metadata"].get("annotations", {}).get(ANNOTATION)
    if annotation == plan_value["new_relay_sha256"][:16]:
        patch_annotation(plan_value, False)
    elif annotation is not None:
        raise ValueError("Unknown Deployment annotation cannot be safely removed")
    state = verify_live(plan_value, False)
    value = {"status": "COLLECTOR_ORIGINAL_CONFIG_RESTORED", "at_utc": datetime.now(timezone.utc).isoformat(),
             "state": state, "original_configmap_sha256": plan_value["original_relay_sha256"]}
    save(output, value)
    return {"status": value["status"], "pod": state["pod_name"]}


def apply(plan_path, health_path, residue_path, output):
    if output.exists():
        raise ValueError("Apply record already exists")
    plan_value = read(plan_path)
    health, residue = read(health_path), read(residue_path)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(health["captured_at_utc"])).total_seconds()
    if (plan_value["status"] != "SINGLE_COLLECTOR_CANDIDATE_PLANNED_NOT_APPLIED" or
            health["status"] != "GLOBAL_SAFETY_PASS" or not 0 <= age <= 180 or
            residue["status"] != "HOST_RESIDUE_PASS" or not timer_active()):
        raise ValueError("Fresh safety, no-fault residue, or independent rollback absent")
    result = {"at_utc": datetime.now(timezone.utc).isoformat(),
              "plan_sha256": sha(plan_path), "health_sha256": sha(health_path),
              "residue_sha256": sha(residue_path), "fault_injected": False}
    try:
        patch_configmap(plan_value["original_relay"], plan_value["new_relay"],
                        plan_value["original_configmap_uid"])
        patch_annotation(plan_value, True)
        result["state"] = verify_live(plan_value, True)
        result["status"] = "COLLECTOR_CANDIDATE_APPLIED"
    except Exception as error:
        result["status"] = "COLLECTOR_CANDIDATE_APPLY_FAILED"
        result["error"] = str(error)
        try:
            result["automatic_rollback"] = rollback(plan_value,
                ROOT / "results/apply-failure-automatic-rollback.json")
        except Exception as rollback_error:
            result["automatic_rollback_error"] = str(rollback_error)
        save(output, result)
        raise
    save(output, result)
    return {"status": result["status"], "pod": result["state"]["pod_name"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("backup", "plan", "apply", "rollback"))
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--prereg", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--health", type=Path)
    parser.add_argument("--residue", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "backup":
        value = backup(args.backup_dir)
    elif args.action == "plan":
        value = plan(args.backup_dir, args.prereg, args.output)
    elif args.action == "apply":
        value = apply(args.plan, args.health, args.residue, args.output)
    else:
        value = rollback(read(args.plan), args.output)
    print(json.dumps(value))


if __name__ == "__main__":
    main()
