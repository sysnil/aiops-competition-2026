#!/usr/bin/env python3
"""Apply a bounded, auto-rollback Envoy transport trial after a clean window ends."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


NAME = "s5v3ap-frontend-proxy-idle-c2"
WATCHDOG = "s5v3ap-proxy-rollback"


def call(*args, timeout=120):
    return subprocess.run(args, check=True, capture_output=True,
                          text=True, timeout=timeout).stdout


def live_deployment():
    return json.loads(call("kubectl", "-n", "otel-demo", "get", "deployment",
                           "frontend-proxy", "-o", "json"))


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def rollback(revision):
    call("kubectl", "-n", "otel-demo", "rollout", "undo",
         "deployment/frontend-proxy", "--to-revision=" + revision)
    call("kubectl", "-n", "otel-demo", "rollout", "status",
         "deployment/frontend-proxy", "--timeout=120s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite a repair trial")
    prepared = json.loads((args.prepared / "manifest.json").read_text())
    if prepared["status"] != "PREPARED_NOT_APPLIED":
        raise ValueError("Repair preparation is incomplete")
    original = json.loads((args.prepared / "original-deployment.json").read_text())
    if digest(original["spec"]["template"]) != digest(live_deployment()["spec"]["template"]):
        raise ValueError("Deployment changed since the original backup")
    template = args.prepared / "frontend-proxy-envoy-c2.tmpl.yaml"
    if hashlib.sha256(template.read_bytes()).hexdigest() != \
            prepared["sha256"]["repaired_template"]:
        raise ValueError("Prepared config hash mismatch")
    args.output.mkdir(parents=True)
    revision = prepared["old_revision"]
    record = {"status": "APPLY_STARTED", "at_utc":
              datetime.now(timezone.utc).isoformat(), "original_revision": revision,
              "template_sha256": prepared["sha256"]["repaired_template"],
              "watchdog": WATCHDOG + ".timer", "configmap": NAME}
    # The timer is independent of this process and restores the original ReplicaSet.
    call("systemd-run", "--quiet", "--on-active=90m", "--unit=" + WATCHDOG,
         "/usr/bin/kubectl", "-n", "otel-demo", "rollout", "undo",
         "deployment/frontend-proxy", "--to-revision=" + revision)
    record["watchdog_active"] = WATCHDOG + ".timer" in call(
        "systemctl", "list-timers", "--all", "--no-legend")
    if not record["watchdog_active"]:
        raise RuntimeError("Automatic rollback timer did not become active")
    patched = False
    try:
        call("kubectl", "-n", "otel-demo", "create", "configmap", NAME,
             "--from-file=envoy.tmpl.yaml=" + str(template))
        patch = {"spec": {"template": {"spec": {
            "volumes": [{"name": "s5v3ap-envoy-template",
                         "configMap": {"name": NAME}}],
            "containers": [{"name": "frontend-proxy", "volumeMounts": [{
                "name": "s5v3ap-envoy-template",
                "mountPath": "/home/envoy/envoy.tmpl.yaml",
                "subPath": "envoy.tmpl.yaml", "readOnly": True}]}]}}}}
        call("kubectl", "-n", "otel-demo", "patch", "deployment",
             "frontend-proxy", "--type=strategic", "-p", json.dumps(patch))
        patched = True
        call("kubectl", "-n", "otel-demo", "rollout", "status",
             "deployment/frontend-proxy", "--timeout=120s")
        current = live_deployment()
        mounts = current["spec"]["template"]["spec"]["containers"][0].get(
            "volumeMounts", [])
        if not any(row.get("name") == "s5v3ap-envoy-template" for row in mounts):
            raise RuntimeError("New proxy has no configuration mount")
        record["status"] = "APPLIED_PENDING_SHORT_NO_FAULT_CHECK"
        record["new_revision"] = current["metadata"]["annotations"].get(
            "deployment.kubernetes.io/revision")
        record["new_template_sha256"] = digest(current["spec"]["template"])
    except Exception as exc:
        record["status"] = "ROLLED_BACK_AFTER_APPLY_FAILURE"
        record["error"] = str(exc)
        if patched:
            rollback(revision)
        call("systemctl", "stop", WATCHDOG + ".timer")
        raise
    finally:
        (args.output / "apply-manifest.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
