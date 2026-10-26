#!/usr/bin/env python3
"""Preserve a failed 45-minute attempt without inventing missing stages."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import subprocess


MASTER = Path("/root/stage5-v3-collector-reliability/runs/CR-CONTINUOUS-45M-20261012")
WORKER = Path("/data/rca-archives/stage5-v3-collector-reliability-20261012/continuous-partial")
MANIFEST = "continuous-partial-source-manifest.json"
WORKERS = {"k8s-worker2": "192.168.53.54", "k8s-worker3": "192.168.53.55"}


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def files(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Attempt source absent or symlink")
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Unexpected symlink in attempt")
        if path.is_file():
            result[str(path.relative_to(root))] = {"bytes": path.stat().st_size,
                "sha256": sha(path)}
    if not {"schedule.json", "monitor.jsonl"} <= set(result):
        raise ValueError("No schedulable partial attempt")
    return result


def inventory(output):
    if output.exists():
        raise ValueError("Refusing overwrite")
    open_handles = subprocess.run(["lsof", "-nP", "+D", str(MASTER)],
        capture_output=True, text=True, timeout=90)
    if open_handles.returncode not in (0, 1) or open_handles.stdout.splitlines()[1:]:
        raise RuntimeError("Partial attempt still has active file handles")
    rows = files(MASTER)
    monitor_lines = sum(1 for line in (MASTER / "monitor.jsonl").open() if line.strip())
    value = {"status": "PARTIAL_ATTEMPT_SOURCE_FROZEN_NOT_RELIABILITY_PASS",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(MASTER), "files": rows, "file_count": len(rows),
        "source_bytes": sum(row["bytes"] for row in rows.values()),
        "monitor_rows_observed": monitor_lines,
        "scheduled_monitor_rows": 180,
        "result_json_present": "result.json" in rows,
        "missing_stages_not_reconstructed": True}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "monitor_rows": monitor_lines,
                      "files": len(rows)}))


def verify(manifest_path, output):
    if output.exists() or socket.gethostname() not in WORKERS:
        raise ValueError("Wrong Worker or existing proof")
    manifest = json.loads(manifest_path.read_text())
    actual = files(WORKER)
    if actual != manifest["files"]:
        raise ValueError("Partial attempt archive hash mismatch")
    schedule = json.loads((WORKER / "schedule.json").read_text())
    with (WORKER / "monitor.jsonl").open() as stream:
        first = json.loads(stream.readline())
    if schedule.get("id") != "CR-CONTINUOUS-45M-20261012" or "planned_utc" not in first:
        raise ValueError("Archived schedule/monitor restoration failed")
    value = {"status": "PARTIAL_ATTEMPT_ARCHIVE_VERIFIED_AND_RESTORED",
        "worker": socket.gethostname(), "worker_ip": WORKERS[socket.gethostname()],
        "source_manifest_sha256": sha(manifest_path), "archive_path": str(WORKER),
        "file_count": len(actual), "source_bytes": sum(row["bytes"] for row in actual.values()),
        "schedule_restored": True, "monitor_first_row_restored": True,
        "verified_utc": datetime.now(timezone.utc).isoformat()}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "worker": value["worker"]}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("inventory", "verify"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "inventory":
        inventory(args.manifest)
    else:
        if not args.output:
            raise ValueError("Proof path required")
        verify(args.manifest, args.output)


if __name__ == "__main__":
    main()
