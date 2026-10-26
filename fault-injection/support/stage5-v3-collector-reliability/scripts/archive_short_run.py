#!/usr/bin/env python3
"""Inventory and independently restore bounded Collector Canary archives."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import subprocess


RUN_NAMES = {"CR-REPAIR-CANARY-1-20261012", "CR-REPAIR-CANARY-2-20261012"}
ROOT = Path("/root/stage5-v3-collector-reliability/runs")
WORKERS = {"k8s-worker2": "192.168.53.54", "k8s-worker3": "192.168.53.55"}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def entries(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Run root absent or symlink")
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlink in run archive: " + str(path))
        if path.is_file():
            stat = path.stat()
            if stat.st_nlink != 1:
                raise ValueError("Externally linked source: " + str(path))
            result[str(path.relative_to(root))] = {"bytes": stat.st_size,
                "sha256": digest(path)}
    return result


def inventory(name, path):
    expected = ROOT / name
    if name not in RUN_NAMES or path.resolve() != expected or not expected.is_dir():
        raise ValueError("Unregistered run")
    opened = subprocess.run(["lsof", "-nP", "+D", str(expected)], capture_output=True,
                            text=True, timeout=90)
    if opened.returncode not in (0, 1) or opened.stdout.splitlines()[1:]:
        raise RuntimeError("Run has open files or lsof failed")
    files = entries(expected)
    if not files or not {"trace-audit/audit.json", "late-audit/late-audit.json"} <= set(files):
        raise ValueError("Audited Canary source incomplete")
    result = {"status": "SOURCE_INVENTORY_FROZEN", "name": name,
        "source": str(expected), "created_utc": datetime.now(timezone.utc).isoformat(),
        "files": files, "file_count": len(files),
        "total_bytes": sum(row["bytes"] for row in files.values())}
    return result


def verify(manifest_path, destination):
    manifest = json.loads(manifest_path.read_text())
    host = socket.gethostname()
    if host not in WORKERS or manifest.get("name") not in RUN_NAMES:
        raise ValueError("Verification must run on registered Worker")
    if not str(destination).startswith("/data/rca-archives/stage5-v3-collector-reliability-20261012/"):
        raise ValueError("Archive outside registered data disk")
    actual = entries(destination)
    if actual != manifest["files"]:
        raise ValueError("Archive file set or SHA-256 mismatch")
    samples = {}
    for relative in ("state-machine/sessions.jsonl", "trace-audit/raw-pages/page-0000.json",
                     "late-audit/raw-pages/page-0000.json"):
        path = destination / relative
        with path.open() as source:
            value = json.loads(source.readline())
        if (relative.endswith("sessions.jsonl") and not value.get("steps")) or (
                relative.endswith("page-0000.json") and "hits" not in value):
            raise ValueError("Archived sample could not be restored: " + relative)
        samples[relative] = {"sha256": digest(path), "json_first_record_read": True}
    return {"status": "ARCHIVE_VERIFIED_AND_RESTORED", "name": manifest["name"],
        "worker": host, "worker_ip": WORKERS[host], "archive": str(destination),
        "source_manifest_sha256": digest(manifest_path),
        "file_count": len(actual),
        "total_bytes": sum(row["bytes"] for row in actual.values()),
        "verified_utc": datetime.now(timezone.utc).isoformat(),
        "restored_samples": samples}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("inventory", "verify"))
    parser.add_argument("--name")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--proof", type=Path)
    args = parser.parse_args()
    if args.action == "inventory":
        if args.manifest.exists():
            raise ValueError("Refusing manifest overwrite")
        result = inventory(args.name, ROOT / args.name)
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(result, indent=2) + "\n")
    else:
        if not args.destination or not args.proof or args.proof.exists():
            raise ValueError("Verification needs new proof and destination")
        result = verify(args.manifest, args.destination)
        args.proof.parent.mkdir(parents=True, exist_ok=True)
        args.proof.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "file_count": result["file_count"],
                      "total_bytes": result["total_bytes"]}))


if __name__ == "__main__":
    main()
