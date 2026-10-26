#!/usr/bin/env python3
"""Bounded systemd rollback rehearsal for one isolated ConfigMap."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

import yaml


NAME = "s5v3-fastready-watchdog-probe-20261013"
NAMESPACE = "otel-demo"
KUBECONFIG = "/etc/kubernetes/admin.conf"
KUBECTL = "/usr/bin/kubectl"
CONTEXT = "kubernetes-admin@kubernetes"
API = "https://192.168.53.52:6443"
NS_UID = "7030ffb7-b5c5-498b-8895-4a58171240bb"
LABEL_KEY = "stage5v3-fastready"
LABEL_VALUE = "watchdog-probe"
MARKER = "20261013"
BASELINE = {"marker": MARKER, "state": "BASELINE"}
CANDIDATE = {"marker": MARKER, "state": "CANDIDATE"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    if path.exists():
        raise ValueError("Refusing overwrite: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def kube(*args, payload=None):
    command = [KUBECTL, "--kubeconfig=" + KUBECONFIG,
               "--context=" + CONTEXT, "-n", NAMESPACE, *args]
    for attempt in range(2):
        result = subprocess.run(command, input=payload, text=True,
            capture_output=True, timeout=25)
        if result.returncode == 0:
            return result.stdout
        if attempt == 0 and any(code in result.stderr for code in (
                "connection refused", "i/o timeout", "TLS handshake timeout")):
            time.sleep(1)
            continue
        raise RuntimeError("kubectl " + " ".join(args[:3]) + ": " +
                           result.stderr[-350:])
    raise RuntimeError("Unreachable retry state")


def cluster():
    if Path(KUBECONFIG).stat().st_mode & 0o077:
        raise ValueError("Kubeconfig permissions are not owner-only")
    context = kube("config", "current-context").strip()
    server = kube("config", "view", "--minify", "-o",
                  "jsonpath={.clusters[0].cluster.server}").strip()
    namespace = json.loads(kube("get", "namespace", NAMESPACE, "-o", "json"))
    if (context != CONTEXT or server != API or
            namespace["metadata"]["uid"] != NS_UID):
        raise ValueError("Kubernetes cluster or namespace identity mismatch")
    return {"context": context, "server": server,
            "namespace_uid": namespace["metadata"]["uid"],
            "kubeconfig_path": KUBECONFIG,
            "kubeconfig_owner_only": True}


def get_object():
    raw = kube("get", "configmap", NAME, "--ignore-not-found", "-o", "json")
    return json.loads(raw) if raw.strip() else None


def stable_pods():
    pods = json.loads(kube("get", "pods", "-o", "json"))["items"]
    prefixes = ("otel-collector-", "frontend-proxy-", "cart-", "checkout-",
                "shipping-", "quote-")
    return {pod["metadata"]["name"]: pod["metadata"]["uid"] for pod in pods
            if pod["metadata"]["name"].startswith(prefixes) and
            pod["status"].get("phase") == "Running"}


def validate_object(obj, registration):
    meta = obj["metadata"]
    if (meta["name"] != NAME or meta["namespace"] != NAMESPACE or
            meta["uid"] != registration["object_uid"] or
            meta.get("labels", {}).get(LABEL_KEY) != LABEL_VALUE or
            meta.get("ownerReferences") or obj.get("immutable") is True or
            obj.get("data") not in (BASELINE, CANDIDATE)):
        raise ValueError("Object identity, ownership, or data differs")


def patch_state(obj, expected, target):
    patch = [
        {"op": "test", "path": "/metadata/uid", "value": obj["metadata"]["uid"]},
        {"op": "test", "path": "/metadata/resourceVersion",
         "value": obj["metadata"]["resourceVersion"]},
        {"op": "test", "path": "/data/state", "value": expected},
        {"op": "replace", "path": "/data/state", "value": target},
    ]
    kube("patch", "configmap", NAME, "--type=json", "-p", json.dumps(patch))


def load_registration(path, expected_sha):
    if file_sha(path) != expected_sha:
        raise ValueError("Registration SHA changed")
    value = json.loads(path.read_text())
    if (value["status"] != "WATCHDOG_TEST_OBJECT_REGISTERED" or
            value["name"] != NAME or value["baseline_data_sha256"] != digest(BASELINE)
            or value["candidate_data_sha256"] != digest(CANDIDATE) or
            value["cluster"]["namespace_uid"] != NS_UID):
        raise ValueError("Unregistered object or data")
    return value


def setup(output, policy_path):
    policy = yaml.safe_load(policy_path.read_text())
    if (policy["test_configmap"] != NAME or policy["api_server"] != API or
            policy["namespace_uid"] != NS_UID or
            policy["status"] != "DRAFT_REHEARSAL_ONLY"):
        raise ValueError("Unexpected rehearsal preregistration")
    identity = cluster()
    if get_object() is not None:
        raise ValueError("Test object already exists")
    deployments = json.loads(kube("get", "deployments", "-o", "json"))["items"]
    if any(volume.get("configMap", {}).get("name") == NAME for dep in deployments
           for volume in dep["spec"]["template"]["spec"].get("volumes", [])):
        raise ValueError("Test object referenced by a Deployment")
    pod_uids = stable_pods()
    obj = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
        "name": NAME, "namespace": NAMESPACE, "labels": {LABEL_KEY: LABEL_VALUE}},
        "data": BASELINE}
    kube("create", "-f", "-", payload=json.dumps(obj))
    live = get_object()
    if live is None or live.get("data") != BASELINE:
        raise ValueError("Created object differs from registration")
    value = {"status": "WATCHDOG_TEST_OBJECT_REGISTERED",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "cluster": identity, "name": NAME, "object_uid": live["metadata"]["uid"],
        "baseline_data_sha256": digest(BASELINE),
        "candidate_data_sha256": digest(CANDIDATE),
        "preregistration_sha256": file_sha(policy_path),
        "business_pod_uids_before": pod_uids,
        "original_object": live,
        "fault_injected": False}
    save(output, value)
    return {"status": value["status"], "uid": value["object_uid"]}


def mutate(registration, output):
    cluster()
    obj = get_object()
    validate_object(obj, registration)
    if obj["data"] != BASELINE:
        raise ValueError("Object is not baseline before test mutation")
    patch_state(obj, "BASELINE", "CANDIDATE")
    live = get_object()
    validate_object(live, registration)
    if live["data"] != CANDIDATE:
        raise ValueError("Test mutation did not take effect")
    value = {"status": "ISOLATED_TEST_OBJECT_MUTATED",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "object_uid": live["metadata"]["uid"],
        "candidate_data_sha256": digest(live["data"]),
        "business_pod_uids_unchanged": stable_pods() == registration[
            "business_pod_uids_before"], "fault_injected": False}
    save(output, value)
    return {"status": value["status"],
            "business_pod_uids_unchanged": value["business_pod_uids_unchanged"]}


def rollback(registration, output):
    result = {"at_utc": datetime.now(timezone.utc).isoformat(),
              "object_uid": registration["object_uid"], "fault_injected": False}
    try:
        result["cluster"] = cluster()
        obj = get_object()
        if obj is None:
            raise ValueError("Registered test object missing")
        validate_object(obj, registration)
        if obj["data"] == CANDIDATE:
            patch_state(obj, "CANDIDATE", "BASELINE")
            result["action"] = "RESTORED_CANDIDATE"
        else:
            result["action"] = "NOOP_BASELINE_ALREADY_PRESENT"
        live = get_object()
        validate_object(live, registration)
        result["restored_data_sha256"] = digest(live["data"])
        result["business_pod_uids_unchanged"] = stable_pods() == registration[
            "business_pod_uids_before"]
        if live["data"] != BASELINE or not result["business_pod_uids_unchanged"]:
            raise RuntimeError("Restored state or business Pod identity mismatch")
        result["status"] = "SYSTEMD_WATCHDOG_ROLLBACK_PASS"
    except Exception as error:
        result["status"] = "SYSTEMD_WATCHDOG_ROLLBACK_FAIL_CLOSED"
        result["error"] = str(error)
    save(output, result)
    print(json.dumps({"status": result["status"], "action": result.get("action"),
                      "error": result.get("error")}))
    if result["status"] != "SYSTEMD_WATCHDOG_ROLLBACK_PASS":
        raise SystemExit(1)


def cleanup(registration, output):
    cluster()
    obj = get_object()
    validate_object(obj, registration)
    if obj["data"] != BASELINE:
        raise ValueError("Refusing cleanup of un-restored test object")
    kube("delete", "configmap", NAME, "--wait=true", "--timeout=30s")
    if get_object() is not None:
        raise RuntimeError("Test object remains after cleanup")
    value = {"status": "ISOLATED_TEST_OBJECT_REMOVED",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "registered_uid": registration["object_uid"],
        "business_pod_uids_unchanged": stable_pods() == registration[
            "business_pod_uids_before"], "fault_injected": False}
    save(output, value)
    print(json.dumps({"status": value["status"],
                      "business_pod_uids_unchanged": value["business_pod_uids_unchanged"]}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("readonly", "setup", "mutate",
                                          "rollback", "cleanup"))
    parser.add_argument("--prereg", type=Path)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--registration-sha256")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    if args.action == "readonly":
        save(args.output, {"status": "SYSTEMD_KUBERNETES_READONLY_PASS",
            "at_utc": datetime.now(timezone.utc).isoformat(),
            "cluster": cluster(), "fault_injected": False})
    elif args.action == "setup":
        print(json.dumps(setup(args.output, args.prereg)))
    else:
        registration = load_registration(args.registration,
                                         args.registration_sha256)
        if args.action == "mutate":
            print(json.dumps(mutate(registration, args.output)))
        elif args.action == "rollback":
            rollback(registration, args.output)
        else:
            cleanup(registration, args.output)


if __name__ == "__main__":
    main()
