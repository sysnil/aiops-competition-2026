"""Stop only processes whose argv is bound to one calibration run directory."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import time


ROOT = Path("/root/stage5-v3-clean-gate-v2")


def cmdline(pid):
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
        return [x.decode(errors="replace") for x in raw.split(b"\0") if x]
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return []


def matching(attempt):
    marker = str(ROOT / "runs" / attempt)
    result = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        pid = int(entry.name)
        args = cmdline(pid)
        if args and (marker in " ".join(args) or ("--run-id" in args and
            args[args.index("--run-id") + 1] == attempt)) and Path(args[0]).name in (
                "python3", "python", "timeout", "iperf3"):
            result.append({"pid": pid, "argv": args})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (not re.fullmatch(r"S5V3AP-CGV2-VAL-[12]", args.id) or
        args.output.exists() or ROOT not in args.output.parents):
        raise ValueError("Unsafe attempt ID or output")
    before = matching(args.id)
    for row in before:
        try:
            os.kill(row["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    time.sleep(2)
    lingering = matching(args.id)
    for row in lingering:
        try:
            os.kill(row["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass
    time.sleep(.2)
    after = matching(args.id)
    value = {"status": "ATTEMPT_PROCESSES_STOPPED" if not after else
             "STOP_INCOMPLETE", "attempt_id": args.id,
             "at_utc": datetime.now(timezone.utc).isoformat(),
             "terminated": before, "forced": lingering, "remaining": after}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "terminated_count": len(before),
                      "remaining_count": len(after)}))
    if after:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
