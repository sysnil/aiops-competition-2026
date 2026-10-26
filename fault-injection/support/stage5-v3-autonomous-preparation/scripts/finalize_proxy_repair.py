#!/usr/bin/env python3
"""Close the proxy trial only after complete raw no-fault diagnostics exist."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


TIMER = "s5v3ap-proxy-rollback-extended.timer"


def call(*args, timeout=120):
    return subprocess.run(args, check=True, capture_output=True,
                          text=True, timeout=timeout).stdout


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pinned-deployment", type=Path, required=True)
    parser.add_argument("--short-diagnostics", type=Path, required=True)
    parser.add_argument("--full-diagnostics", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or len(args.full_diagnostics) != 4:
        raise ValueError("Exactly four full post-repair windows and a new output are required")
    timer = subprocess.run(["systemctl", "is-active", TIMER], capture_output=True,
                           text=True, timeout=10)
    if timer.stdout.strip() != "active":
        raise RuntimeError("Independent rollback watchdog is not active")
    expected = json.loads(args.pinned_deployment.read_text())
    current = json.loads(call("kubectl", "-n", "otel-demo", "get",
                              "deployment", "frontend-proxy", "-o", "json"))
    if digest(current["spec"]["template"]) != digest(expected["spec"]["template"]):
        raise ValueError("Proxy Deployment template changed since the validated trial")
    files = [args.short_diagnostics, *args.full_diagnostics]
    diagnostics = [json.loads(path.read_text()) for path in files]
    if any(row.get("status") != "DIAGNOSTICS_CAPTURED" for row in diagnostics):
        raise ValueError("A raw proxy diagnostics window is missing")
    counts = {str(path): row["proxy_503_uc_count"] for path, row in zip(files, diagnostics)}
    if all(value == 0 for value in counts.values()):
        call("systemctl", "stop", TIMER)
        status = "REPAIR_RETAINED_WATCHDOG_DISARMED"
    else:
        call("kubectl", "-n", "otel-demo", "rollout", "undo",
             "deployment/frontend-proxy", "--to-revision=1")
        call("kubectl", "-n", "otel-demo", "rollout", "status",
             "deployment/frontend-proxy", "--timeout=120s")
        call("systemctl", "stop", TIMER)
        status = "REPAIR_ROLLED_BACK_AFTER_RECURRENT_503"
    after = json.loads(call("kubectl", "-n", "otel-demo", "get",
                            "deployment", "frontend-proxy", "-o", "json"))
    timer_status = subprocess.run(["systemctl", "is-active", TIMER],
        capture_output=True, text=True, timeout=10).stdout.strip()
    if timer_status == "active":
        raise RuntimeError("Rollback timer remained active after finalization")
    result = {"status": status, "at_utc": datetime.now(timezone.utc).isoformat(),
        "proxy_503_by_window": counts,
        "diagnostic_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in files},
        "pinned_deployment_sha256": hashlib.sha256(
            args.pinned_deployment.read_bytes()).hexdigest(),
        "final_deployment_template_sha256": digest(after["spec"]["template"]),
        "final_deployment_revision": after["metadata"]["annotations"].get(
            "deployment.kubernetes.io/revision"),
        "rollback_timer_status": timer_status,
        "formal_case_created": False, "faults_injected": 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
