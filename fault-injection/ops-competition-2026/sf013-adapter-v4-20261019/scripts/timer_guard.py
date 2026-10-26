#!/usr/bin/env python3
"""Independent Master-side guard for an Attempt's Worker timer."""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from handshake import atomic
from master_transport import remote
from run_registry import ROOT, load_bundle, stamp
sys.path.insert(0, '/root/ops-competition-2026/final-case-fail-closed-v1-20261019/scripts')
from formal_state import revoke as revoke_live_auth


def now():
    return datetime.now(timezone.utc).isoformat()


def unit_active(unit):
    result = subprocess.run(["systemctl", "is-active", unit + ".service"],
                            capture_output=True, text=True, timeout=8)
    return result.returncode == 0 and result.stdout.strip() == "active"


def cancel_bound_timer(manifest_path, manifest_sha, action):
    manifest, _ = load_bundle(manifest_path, manifest_sha)
    auth_dir = (Path('/root/ops-competition-2026/final-case-fail-closed-v1-20261019/runtime') /
                manifest['run_id'] / action['action_id'])
    revoke_live_auth(auth_dir, 'MASTER_GUARD_CANCEL')
    output = remote("w2", ["python3", "-B", ROOT / "scripts/fault_worker.py",
                            "cancel", "--manifest", manifest_path,
                            "--manifest-sha", manifest_sha,
                            "--action-id", action["action_id"]], timeout=30)
    value = json.loads(output.splitlines()[-1])
    if value.get("status") != "ATTEMPT_CLEANUP_VERIFIED":
        raise RuntimeError("WORKER_CANCEL_NOT_VERIFIED")
    return value


def watch_controller(controller_unit, terminal, started_file, deadline,
                     cancel, capture_unit=None, poll_seconds=0.5,
                     startup_grace=15):
    started = time.monotonic()
    seen_controller = Path(started_file).is_file()
    while time.time() < deadline:
        active = unit_active(controller_unit)
        seen_controller |= active or Path(started_file).is_file()
        if Path(terminal).is_file():
            controller = json.loads(Path(terminal).read_text())
            receipt = cancel()
            status = ("GUARD_VERIFIED_CONTROLLER_CLEANUP" if
                      controller.get("worker_cleanup_verified") is True else
                      "GUARD_RECOVERED_CONTROLLER_ABORT")
            return {"status": status, "controller_status": controller.get("status"),
                    "worker_cancel": receipt}
        if not active and (seen_controller or time.monotonic() - started >= startup_grace):
            receipt = cancel()
            if capture_unit:
                subprocess.run(["systemctl", "stop", capture_unit + ".service"],
                               capture_output=True, text=True, timeout=20)
            return {"status": "GUARD_ABORTED_ORPHANED_CONTROLLER",
                    "worker_cancel": receipt}
        time.sleep(poll_seconds)
    return {"status": "GUARD_DEADLINE_ABORT", "worker_cancel": cancel()}


def guard(manifest_path, manifest_sha, poll_seconds=0.5, startup_grace=15):
    manifest, plan = load_bundle(manifest_path, manifest_sha)
    action = manifest["actions"][0]
    control = Path(plan["control_root"])
    output = control / "timer-guard-terminal.json"
    if output.exists():
        raise RuntimeError("GUARD_TERMINAL_ALREADY_EXISTS")
    observed = watch_controller(
        plan["controller_unit"], control / "fault-controller-terminal.json",
        control / "fault-controller-started.json",
        stamp(plan["start_utc"]) + plan["duration_seconds"] + 60,
        lambda: cancel_bound_timer(manifest_path, manifest_sha, action),
        capture_unit=plan["capture_controller_unit"],
        poll_seconds=poll_seconds, startup_grace=startup_grace)
    record = {**observed, "at_utc": now(), "run_id": manifest["run_id"],
              "action_id": action["action_id"]}
    atomic(output, record, True)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha", required=True)
    args = parser.parse_args()
    try:
        record = guard(args.manifest, args.manifest_sha)
        print(json.dumps(record), flush=True)
    except BaseException as exc:
        control = Path(load_bundle(args.manifest, args.manifest_sha)[1]["control_root"])
        evidence = {"status": "CLEANUP_UNVERIFIED", "at_utc": now(),
                    "error": type(exc).__name__ + ": " + str(exc)}
        failure = control / "timer-guard-failure.json"
        if not failure.exists():
            atomic(failure, evidence, True)
        print(json.dumps(evidence), flush=True)
        raise


if __name__ == "__main__":
    main()
