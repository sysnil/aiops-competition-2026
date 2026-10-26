#!/usr/bin/env python3
"""Guard and run the existing public no-fault live collector on mounted storage."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from data_disk_guard import assert_ready


SOURCE = Path("/root/stage5-v3-autonomous-preparation/scripts/capture_live_c2.py")
DATA_CAPTURE = Path("/data/stage5-v3/fast-ready/capture")
ROOT = Path("/root/stage5-v3-fast-ready")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    if path.exists():
        raise ValueError("Refusing overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.run.name != "S5V3FR-DISK-CANARY-20261013" or (
            args.output != DATA_CAPTURE / args.run.name):
        raise ValueError("Only the registered new no-fault data-disk Canary is supported")
    disk = assert_ready(args.output, 5 * 1024 ** 3)
    assert_ready(args.run)
    schedule = json.loads((args.run / "schedule.json").read_text())
    if schedule["id"] != args.run.name or schedule["fault_injected"]:
        raise ValueError("Unregistered or faulted capture source")
    command = ["/usr/bin/python3", "-B", str(SOURCE), "--phase", "during",
        "--start-utc", schedule["scheduled_start_utc"], "--end-utc",
        schedule["scheduled_end_utc"], "--trace-source",
        "JAEGER_OPENSEARCH_STORAGE", "--wait-until-end", "--output",
        str(args.output)]
    result = {"status": "LIVE_CAPTURE_FAILED", "at_utc": datetime.now(
        timezone.utc).isoformat(), "run_id": schedule["id"],
        "data_disk_guard": disk, "source_sha256": {str(path): sha(path)
        for path in (SOURCE, args.run / "schedule.json", Path(__file__))},
        "command_argv": command, "fault_injected": False}
    try:
        run = subprocess.run(command, capture_output=True, text=True, timeout=360)
        result.update({"exit_code": run.returncode, "stdout_tail": run.stdout[-1200:],
            "stderr_tail": run.stderr[-1600:],
            "completed_utc": datetime.now(timezone.utc).isoformat()})
        manifest_path = args.output / "capture-manifest.json"
        if run.returncode == 0 and manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text())
            required = ("metrics", "logs", "trace-storage-pages", "kubernetes")
            layout = {name: len(list((args.output / name).glob("*"))) for name in required}
            if (manifest["trace_source"] == "JAEGER_OPENSEARCH_STORAGE" and
                    manifest["window"]["start_utc"] == schedule["scheduled_start_utc"]
                    and manifest["window"]["end_utc"] == schedule["scheduled_end_utc"]
                    and all(layout.values()) and
                    args.output.stat().st_dev == Path("/data/stage5-v3").stat().st_dev):
                result.update({"status": "LIVE_METRICS_LOGS_TRACES_CAPTURE_PASS",
                    "manifest_sha256": sha(manifest_path), "layout_counts": layout,
                    "trace_records": manifest["trace_records"],
                    "span_records": manifest["span_records"],
                    "log_records": manifest["log_records"],
                    "capture_start_lag_seconds": manifest["start_lag_seconds"],
                    "capture_completion_lag_seconds": manifest[
                        "completion_lag_seconds"]})
    except Exception as error:
        result["error"] = str(error)
    result["completed_utc"] = datetime.now(timezone.utc).isoformat()
    save(ROOT / "results/data-disk-live-capture.json", result)
    print(json.dumps({"status": result["status"],
        "trace_records": result.get("trace_records"),
        "log_records": result.get("log_records"),
        "error": result.get("error")}))
    if result["status"] != "LIVE_METRICS_LOGS_TRACES_CAPTURE_PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
