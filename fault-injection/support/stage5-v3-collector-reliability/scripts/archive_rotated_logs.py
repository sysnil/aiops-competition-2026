#!/usr/bin/env python3
"""Safely relocate two inactive rotated host logs to dual Worker archives."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tarfile


ROOT = Path("/root/stage5-v3-collector-reliability")
SOURCES = (Path("/var/log/syslog.1"), Path("/var/log/kern.log.1"))
MANIFEST = ROOT / "archive/rotated-host-logs-source.json"
ARCHIVE = ROOT / "archive/rotated-host-logs.tar.gz"
WORKER_ROOT = Path("/data/rca-archives/stage5-v3-collector-reliability-20261012")
WORKERS = {"k8s-worker2": "192.168.53.54", "k8s-worker3": "192.168.53.55"}


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified_source():
    results = {}
    for path in SOURCES:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Unexpected rotated log: " + str(path))
        process = subprocess.run(["lsof", "-nP", str(path)], capture_output=True,
                                 text=True, timeout=20)
        if process.returncode not in (0, 1) or process.stdout.splitlines()[1:]:
            raise RuntimeError("Rotated log is actively open")
        stat = path.stat()
        results[str(path)] = {"sha256": sha(path), "bytes": stat.st_size,
                              "mtime_ns": stat.st_mtime_ns}
    return results


def verify_tar(path, manifest):
    with tarfile.open(path, "r:gz") as archive:
        members = {item.name: item for item in archive.getmembers() if item.isfile()}
        expected = {name.lstrip("/") for name in manifest["files"]}
        if set(members) != expected:
            raise ValueError("Rotated log archive member set mismatch")
        for absolute, row in manifest["files"].items():
            payload = archive.extractfile(members[absolute.lstrip("/")]).read()
            if len(payload) != row["bytes"] or sha_bytes(payload) != row["sha256"]:
                raise ValueError("Rotated log restore hash mismatch: " + absolute)


def create():
    if ARCHIVE.exists() or MANIFEST.exists():
        raise ValueError("Refusing archive overwrite")
    files = verified_source()
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(ARCHIVE, "w:gz") as output:
        for path in SOURCES:
            output.add(path, arcname=str(path).lstrip("/"), recursive=False)
    value = {"status": "INACTIVE_ROTATED_LOG_SOURCE_FROZEN",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "files": files, "source_bytes": sum(row["bytes"] for row in files.values()),
        "archive_sha256": sha(ARCHIVE), "archive_bytes": ARCHIVE.stat().st_size}
    verify_tar(ARCHIVE, value)
    MANIFEST.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "source_bytes": value["source_bytes"],
                      "archive_bytes": value["archive_bytes"]}))


def verify(proof):
    if proof.exists() or socket.gethostname() not in WORKERS:
        raise ValueError("Wrong Worker or existing proof")
    manifest = json.loads((WORKER_ROOT / MANIFEST.name).read_text())
    tar = WORKER_ROOT / ARCHIVE.name
    if sha(tar) != manifest["archive_sha256"]:
        raise ValueError("Worker compressed archive hash mismatch")
    verify_tar(tar, manifest)
    value = {"status": "ROTATED_LOGS_RESTORED_AND_VERIFIED",
        "worker": socket.gethostname(), "worker_ip": WORKERS[socket.gethostname()],
        "archive_path": str(tar), "archive_sha256": manifest["archive_sha256"],
        "source_manifest_sha256": sha(WORKER_ROOT / MANIFEST.name),
        "restored_file_count": len(manifest["files"]),
        "restored_bytes": manifest["source_bytes"],
        "verified_utc": datetime.now(timezone.utc).isoformat()}
    proof.parent.mkdir(parents=True, exist_ok=True)
    proof.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "worker": value["worker"]}))


def release(proofs, output):
    if output.exists() or len(proofs) != 2:
        raise ValueError("New output and two proofs required")
    manifest = json.loads(MANIFEST.read_text())
    if sha(ARCHIVE) != manifest["archive_sha256"]:
        raise ValueError("Master compressed archive changed")
    verify_tar(ARCHIVE, manifest)
    values = [json.loads(path.read_text()) for path in proofs]
    if {row["worker_ip"] for row in values} != set(WORKERS.values()) or any(
            row["status"] != "ROTATED_LOGS_RESTORED_AND_VERIFIED" or
            row["archive_sha256"] != manifest["archive_sha256"] or
            row["source_manifest_sha256"] != sha(MANIFEST) or
            row["restored_file_count"] != len(SOURCES) for row in values):
        raise ValueError("Independent restored copies not verified")
    if verified_source() != manifest["files"]:
        raise ValueError("Original rotated logs changed after archive")
    for path in SOURCES:
        path.unlink()
    value = {"status": "ROTATED_MASTER_LOG_DUPLICATES_RELEASED",
        "released_utc": datetime.now(timezone.utc).isoformat(),
        "source_manifest_sha256": sha(MANIFEST), "archive_sha256": manifest["archive_sha256"],
        "released_bytes": manifest["source_bytes"],
        "archive_on_master": str(ARCHIVE),
        "worker_archives": [row["archive_path"] for row in values]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "released_bytes": value["released_bytes"]}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("create", "verify", "release"))
    parser.add_argument("--proof", type=Path, action="append")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "create":
        create()
    elif args.action == "verify":
        if not args.output:
            raise ValueError("Worker proof path required")
        verify(args.output)
    else:
        if not args.output:
            raise ValueError("Release record required")
        release(args.proof or [], args.output)


if __name__ == "__main__":
    main()
