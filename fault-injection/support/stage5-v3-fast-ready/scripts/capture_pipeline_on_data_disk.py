#!/usr/bin/env python3
"""DRAFT 10+5+10 public capture entrypoint with strict data-disk output."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from data_disk_guard import assert_ready


ROOT = Path("/root/stage5-v3-fast-ready")
PIPELINE = Path("/root/stage5-v3-clean-gate-v2/scripts/capture_pipeline_v3.py")
RUNS = Path("/data/stage5-v3/fast-ready/runs")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--start-utc", required=True)
    parser.add_argument("--prereg", type=Path, required=True)
    args = parser.parse_args()
    if not args.run_id.startswith("S5V3FR-") or not args.run_id.replace("-", "").isalnum():
        raise ValueError("Unregistered DRAFT run id format")
    prereg = json.loads(args.prereg.read_text())
    if (prereg.get("status") != "PREREGISTERED_NO_FAULT_CLEAN" or
            prereg.get("id") != args.run_id or prereg.get("fault_injected") is not False):
        raise ValueError("Clean capture preregistration absent")
    start = datetime.fromisoformat(args.start_utc.replace("Z", "+00:00"))
    if start.tzinfo is None or start.astimezone(timezone.utc).isoformat() != prereg[
            "scheduled_start_utc"]:
        raise ValueError("Clean capture start differs from preregistration")
    output = RUNS / args.run_id / "capture-pipeline"
    if output.exists():
        raise ValueError("Refusing output overwrite")
    disk = assert_ready(output, prereg["planned_raw_bytes"])
    command = ["/usr/bin/python3", "-B", str(PIPELINE), "--start",
               start.isoformat(), "--output", str(output)]
    run = subprocess.run(command, capture_output=True, text=True, timeout=2100)
    result = {"status": "DATA_DISK_FULL_CAPTURE_PASS" if run.returncode == 0
        and (output / "pipeline-result.json").is_file() else
        "DATA_DISK_FULL_CAPTURE_FAIL", "at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id, "output": str(output), "data_disk_guard": disk,
        "capture_source_sha256": sha(PIPELINE), "prereg_sha256": sha(args.prereg),
        "exit_code": run.returncode, "stdout_tail": run.stdout[-1400:],
        "stderr_tail": run.stderr[-1800:], "fault_injected": False}
    target = ROOT / "results" / (args.run_id + "-capture-status.json")
    if target.exists():
        raise ValueError("Refusing status overwrite")
    target.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "run_id": args.run_id}))
    if run.returncode:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
