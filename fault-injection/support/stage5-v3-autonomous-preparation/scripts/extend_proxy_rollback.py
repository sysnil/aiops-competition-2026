#!/usr/bin/env python3
"""Replace a short proxy repair rollback timer with a verified long watchdog."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


OLD = "s5v3ap-proxy-rollback.timer"
NEW = "s5v3ap-proxy-rollback-extended.timer"


def call(*args):
    return subprocess.run(args, check=True, capture_output=True,
                          text=True, timeout=30).stdout


def template_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def active(unit):
    return subprocess.run(["systemctl", "is-active", unit], capture_output=True,
                          text=True, timeout=10).stdout.strip() == "active"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pinned-deployment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Watchdog extension record already exists")
    pinned = json.loads(args.pinned_deployment.read_text())
    current = json.loads(call("kubectl", "-n", "otel-demo", "get",
                              "deployment", "frontend-proxy", "-o", "json"))
    if template_hash(current["spec"]["template"]) != \
            template_hash(pinned["spec"]["template"]):
        raise ValueError("Proxy repair changed before extending rollback")
    before = call("systemctl", "list-timers", "--all", "--no-pager")
    if not active(OLD) or active(NEW):
        raise ValueError("Expected old watchdog missing or extension already active")
    call("systemd-run", "--quiet", "--on-active=7h",
         "--unit=s5v3ap-proxy-rollback-extended", "/usr/bin/kubectl",
         "-n", "otel-demo", "rollout", "undo", "deployment/frontend-proxy",
         "--to-revision=1")
    middle = call("systemctl", "list-timers", "--all", "--no-pager")
    if not active(NEW):
        raise RuntimeError("Extended rollback was not armed; old timer remains")
    call("systemctl", "stop", OLD)
    after = call("systemctl", "list-timers", "--all", "--no-pager")
    if not active(NEW) or active(OLD):
        raise RuntimeError("Watchdog handoff is not singly active")
    result = {"status": "ROLLBACK_WATCHDOG_EXTENDED",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "old_timer": OLD, "new_timer": NEW,
        "new_relative_deadline": "7h", "rollback_revision": "1",
        "deployment_template_sha256": template_hash(current["spec"]["template"]),
        "timer_listing_before_sha256": hashlib.sha256(before.encode()).hexdigest(),
        "timer_listing_after_sha256": hashlib.sha256(after.encode()).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
