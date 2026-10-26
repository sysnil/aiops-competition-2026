#!/usr/bin/env python3
"""Freeze and verify dual Worker copies of the continuous raw capture tree."""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import socket


ROOT = Path("/data/rca-archives/stage5-v3-collector-reliability-20261012")
TREE = ROOT / "continuous-primary"
MANIFEST = ROOT / "continuous-source-manifest.json"
WORKERS = {"k8s-worker2": "192.168.53.54", "k8s-worker3": "192.168.53.55"}


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def inventory_files():
    if TREE.is_symlink() or not TREE.is_dir():
        raise ValueError("Continuous tree absent or symlink")
    files = {}
    for path in TREE.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlink in continuous archive")
        if path.is_file():
            files[str(path.relative_to(TREE))] = {"bytes": path.stat().st_size,
                "sha256": sha(path)}
    required = {"issued/schedule.json", "issued/state-machine/sessions.jsonl",
        "issued/controls/image_asset.jsonl", "issued/controls/cart_view.jsonl",
        "trace-120/capture-manifest.json", "trace-240/capture-manifest.json",
        "comparison.json"}
    if not required <= set(files) or len(files) < 10:
        raise ValueError("Continuous archive missing required evidence")
    return files


def restored_sample():
    for relative in ("trace-120/page-00000.json.gz", "trace-240/page-00000.json.gz"):
        path = TREE / relative
        with gzip.open(path, "rt", encoding="utf-8") as source:
            value = json.load(source)
        if "hits" not in value or "_shards" not in value:
            raise ValueError("Compressed trace page cannot be restored")
    with (TREE / "issued/state-machine/sessions.jsonl").open() as source:
        row = json.loads(source.readline())
    if not row.get("steps"):
        raise ValueError("Issued session cannot be restored")
    return {"session_steps": len(row["steps"]), "trace_pages_restored": 2}


def create():
    if socket.gethostname() != "k8s-worker2" or MANIFEST.exists():
        raise ValueError("Only Worker2 may freeze primary source once")
    files = inventory_files()
    sample = restored_sample()
    value = {"status": "CONTINUOUS_SOURCE_INVENTORY_FROZEN",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "root": str(TREE), "files": files,
        "file_count": len(files), "total_bytes": sum(row["bytes"] for row in files.values()),
        "restore_sample": sample}
    MANIFEST.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "files": len(files),
                      "bytes": value["total_bytes"]}))


def verify(output):
    if output.exists() or socket.gethostname() not in WORKERS:
        raise ValueError("New Worker proof required")
    manifest = json.loads(MANIFEST.read_text())
    actual = inventory_files()
    if actual != manifest["files"]:
        raise ValueError("File set, bytes or SHA-256 differ from primary")
    sample = restored_sample()
    value = {"status": "CONTINUOUS_ARCHIVE_VERIFIED_AND_RESTORED",
        "worker": socket.gethostname(), "worker_ip": WORKERS[socket.gethostname()],
        "verified_utc": datetime.now(timezone.utc).isoformat(),
        "archive_path": str(TREE), "source_manifest_sha256": sha(MANIFEST),
        "file_count": len(actual), "total_bytes": sum(row["bytes"] for row in actual.values()),
        "restore_sample": sample}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "worker": value["worker"],
                      "files": value["file_count"]}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("create", "verify"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "create":
        create()
    else:
        if not args.output:
            raise ValueError("Verification proof path required")
        verify(args.output)


if __name__ == "__main__":
    main()
