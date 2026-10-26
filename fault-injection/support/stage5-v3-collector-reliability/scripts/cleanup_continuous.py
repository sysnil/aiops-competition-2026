#!/usr/bin/env python3
"""Independent timeout cleanup for only the registered no-fault load processes."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import time


ALLOWED = {
    "/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py",
    "/root/stage5-v3-autonomous-preparation/scripts/fixed_control_load_c3.py",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (args.output.exists() or args.state != Path(
            "/root/stage5-v3-collector-reliability/runtime/continuous-processes.json")):
        raise ValueError("Unregistered cleanup state")
    state = json.loads(args.state.read_text())
    actions = {}
    for name, item in state["processes"].items():
        pid = item["pid"]
        command = Path("/proc") / str(pid) / "cmdline"
        if not command.exists():
            actions[name] = "ALREADY_EXITED"
            continue
        raw = command.read_bytes().decode(errors="replace").split("\0")
        if not any(token in ALLOWED for token in raw):
            actions[name] = "PID_REUSED_REFUSED_TO_KILL"
            continue
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        if command.exists():
            os.kill(pid, signal.SIGKILL)
            actions[name] = "KILLED_AFTER_TERM"
        else:
            actions[name] = "TERMINATED"
    result = {"status": "REGISTERED_CONTINUOUS_PROCESS_CLEANUP_COMPLETE",
        "at_utc": datetime.now(timezone.utc).isoformat(), "actions": actions,
        "fault_state_modified": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "actions": actions}))


if __name__ == "__main__":
    main()
