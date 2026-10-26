#!/usr/bin/env python3
"""Keep the repaired proxy at its pretrial Worker2 site for route controls."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


def call(*args, timeout=120):
    return subprocess.run(args, check=True, capture_output=True,
                          text=True, timeout=timeout).stdout


def put(path, value):
    data = (json.dumps(value, indent=2) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(data)
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Pin trial archive must be new")
    timer = call("systemctl", "list-timers", "s5v3ap-proxy-rollback.timer",
                 "--all", "--no-pager")
    if "s5v3ap-proxy-rollback.timer" not in timer:
        raise RuntimeError("Independent rollback timer is absent")
    original = json.loads(call("kubectl", "-n", "otel-demo", "get", "deployment",
                               "frontend-proxy", "-o", "json"))
    if not any(row.get("name") == "s5v3ap-envoy-template" for row in
               original["spec"]["template"]["spec"]["containers"][0]
               .get("volumeMounts", [])):
        raise ValueError("Repaired Envoy template is not the current Deployment")
    args.output.mkdir(parents=True)
    before_hash = put(args.output / "deployment-before-pin.json", original)
    patch = {"spec": {"template": {"spec": {
        "nodeSelector": {"kubernetes.io/hostname": "k8s-worker2"}}}}}
    call("kubectl", "-n", "otel-demo", "patch", "deployment", "frontend-proxy",
         "--type=strategic", "-p", json.dumps(patch))
    call("kubectl", "-n", "otel-demo", "rollout", "status",
         "deployment/frontend-proxy", "--timeout=120s")
    pods = json.loads(call("kubectl", "-n", "otel-demo", "get", "pods",
                           "-l", "opentelemetry.io/name=frontend-proxy", "-o", "json"))
    ready = [row for row in pods["items"] if not row["metadata"].get("deletionTimestamp")
             and all(s.get("ready") for s in row["status"].get("containerStatuses", []))]
    if len(ready) != 1 or ready[0]["spec"]["nodeName"] != "k8s-worker2":
        raise RuntimeError("Repaired proxy did not return uniquely to Worker2")
    final = json.loads(call("kubectl", "-n", "otel-demo", "get", "deployment",
                            "frontend-proxy", "-o", "json"))
    after_hash = put(args.output / "deployment-after-pin.json", final)
    pod_hash = put(args.output / "pods-after-pin.json", pods)
    result = {"status": "PROXY_PIN_WORKER2_PASS", "at_utc":
        datetime.now(timezone.utc).isoformat(), "pod": ready[0]["metadata"]["name"],
        "pod_uid": ready[0]["metadata"]["uid"],
        "pod_node": ready[0]["spec"]["nodeName"],
        "deployment_revision": final["metadata"]["annotations"].get(
            "deployment.kubernetes.io/revision"),
        "rollback_timer_active": True,
        "sha256": {"before": before_hash, "after": after_hash, "pods": pod_hash}}
    put(args.output / "manifest.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
