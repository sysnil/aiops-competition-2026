#!/usr/bin/env python3
"""Inventory and restore-check one immutable no-fault Canary on two Workers."""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import socket
import subprocess


MASTER_DATA_ROOT = Path("/data/stage5-v3/fast-ready/runs")
WORKER_DATA_ROOT = Path("/data/rca-archives/stage5-v3-fast-ready-20261013")
MANIFEST = "canary-source-manifest.json"
STAGES = ("first_plus5", "supplement_plus60", "scoring_plus120", "audit_plus240")
WORKERS = {"k8s-worker2": "192.168.53.54", "k8s-worker3": "192.168.53.55"}


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(root, run_id):
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Canary source absent or symlink")
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Unexpected symlink")
        if path.is_file():
            files[str(path.relative_to(root))] = {"bytes": path.stat().st_size,
                "sha256": sha(path)}
    schedule = json.loads((root / "schedule.json").read_text())
    workload = json.loads((root / "result.json").read_text())
    trace = json.loads((root / "trace/trace-schedule.json").read_text())
    if (schedule["id"] != run_id or
            workload["status"] != "CANARY_WORKLOAD_COMPLETE" or
            trace["status"] != "TIMED_TRACE_CAPTURE_READY" or
            set(trace["stages"]) != set(STAGES) or
            any("trace/" + stage + "/stage-manifest.json" not in files
                for stage in STAGES)):
        raise ValueError("Canary workload or timed capture incomplete")
    return files


def restore(root):
    with (root / "state-machine/sessions.jsonl").open() as stream:
        session = json.loads(stream.readline())
    with (root / "controls/image_asset.jsonl").open() as stream:
        control = json.loads(stream.readline())
    pages = {}
    for stage in STAGES:
        names = sorted((root / "trace" / stage).glob("page-*.json.gz"))
        if not names:
            raise ValueError("Missing Trace page: " + stage)
        with gzip.open(names[0], "rt", encoding="utf-8") as stream:
            page = json.load(stream)
        if not page["hits"]["hits"] or not all("traceID" in hit["_source"]
                and "spanID" in hit["_source"] for hit in page["hits"]["hits"]):
            raise ValueError("Invalid restored Trace page")
        pages[stage] = {"page": names[0].name, "hits": len(page["hits"]["hits"])}
    if not session.get("steps") or not control.get("trace_id"):
        raise ValueError("Issued request restoration failed")
    return {"session_index": session["index"], "control_trace_id": control["trace_id"],
            "pages": pages}


def save(path, value):
    if path.exists():
        raise ValueError("Refusing overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def create(run_id, path):
    if socket.gethostname() != "k8s-master":
        raise ValueError("Source inventory must run on Master")
    from data_disk_guard import assert_ready
    source = MASTER_DATA_ROOT / run_id
    assert_ready(source)
    handles = subprocess.run(["lsof", "-nP", "+D", str(source)],
        capture_output=True, text=True, timeout=90)
    if handles.returncode not in (0, 1) or handles.stdout.splitlines()[1:]:
        raise RuntimeError("Canary still has active writers")
    files = inventory(source, run_id)
    value = {"status": "CANARY_SOURCE_FROZEN", "created_utc":
        datetime.now(timezone.utc).isoformat(), "source": str(source),
        "files": files, "file_count": len(files),
        "source_bytes": sum(row["bytes"] for row in files.values()),
        "sample_restore": restore(source),
        "fault_injected": False, "formal_case": False}
    save(path, value)
    print(json.dumps({"status": value["status"],
        "files": value["file_count"], "bytes": value["source_bytes"]}))


def verify(run_id, manifest, output):
    if socket.gethostname() not in WORKERS:
        raise ValueError("Unknown Worker archive host")
    source = json.loads(manifest.read_text())
    worker = WORKER_DATA_ROOT / run_id
    if source.get("source") != str(MASTER_DATA_ROOT / run_id):
        raise ValueError("Master source is not the approved data disk")
    files = inventory(worker, run_id)
    restored = restore(worker)
    if files != source["files"] or restored != source["sample_restore"]:
        raise ValueError("Dual archive SHA or restoration mismatch")
    value = {"status": "CANARY_ARCHIVE_VERIFIED_AND_RESTORED",
        "verified_utc": datetime.now(timezone.utc).isoformat(),
        "worker": socket.gethostname(), "worker_ip": WORKERS[socket.gethostname()],
        "archive_path": str(worker), "source_manifest_sha256": sha(manifest),
        "file_count": len(files), "source_bytes": source["source_bytes"],
        "sample_restore": restored}
    save(output, value)
    print(json.dumps({"status": value["status"], "worker": value["worker"]}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("create", "verify"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.run_id.startswith("S5V3FR-") or not args.run_id.replace("-", "").isalnum():
        raise ValueError("Unregistered run id format")
    if args.action == "create":
        create(args.run_id, args.manifest)
    elif args.output:
        verify(args.run_id, args.manifest, args.output)
    else:
        raise ValueError("Worker proof output required")


if __name__ == "__main__":
    main()
