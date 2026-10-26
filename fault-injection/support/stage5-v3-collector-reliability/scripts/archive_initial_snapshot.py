#!/usr/bin/env python3
"""Archive read-only pipeline evidence before releasing its Master duplicate."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tarfile


ROOT = Path("/root/stage5-v3-collector-reliability")
RELATIVE_ROOTS = (Path("snapshots/pipeline-initial"), Path("snapshots/gateway-raw-series"))
MANIFEST = ROOT / "archive/initial-snapshot-source.json"
ARCHIVE = ROOT / "archive/initial-snapshot.tar.gz"
WORKER_ROOT = Path("/data/rca-archives/stage5-v3-collector-reliability-20261012")
WORKERS = {"k8s-worker2": ("192.168.53.54", "/tmp/stage5v3-w2-archive.sock"),
           "k8s-worker3": ("192.168.53.55", "/tmp/stage5v3-w3-archive.sock")}


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_files():
    result = {}
    for relative_root in RELATIVE_ROOTS:
        root = ROOT / relative_root
        if root.is_symlink() or not root.is_dir():
            raise ValueError("Snapshot source absent or symlink: " + str(root))
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("Symlink source: " + str(path))
            if path.is_file():
                result[str(path.relative_to(ROOT))] = path
    if len(result) < 40:
        raise ValueError("Read-only snapshot incomplete")
    return result


def verify_archive(archive, manifest):
    expected = manifest["files"]
    with tarfile.open(archive, "r:gz") as stream:
        members = {item.name: item for item in stream.getmembers() if item.isfile()}
        if set(members) != set(expected):
            raise ValueError("Compressed source file set mismatch")
        for relative, row in expected.items():
            content = stream.extractfile(members[relative]).read()
            if len(content) != row["bytes"] or sha_bytes(content) != row["sha256"]:
                raise ValueError("Compressed source bytes differ: " + relative)


def create():
    if MANIFEST.exists() or ARCHIVE.exists():
        raise ValueError("Refusing overwrite")
    files = source_files()
    entries = {name: {"bytes": path.stat().st_size, "sha256": sha(path)}
               for name, path in files.items()}
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(ARCHIVE, "w:gz") as archive:
        for relative, path in sorted(files.items()):
            archive.add(path, arcname=relative, recursive=False)
    value = {"status": "SOURCE_ARCHIVE_FROZEN", "created_utc": datetime.now(timezone.utc).isoformat(),
             "source_root": str(ROOT), "files": entries, "file_count": len(entries),
             "source_total_bytes": sum(row["bytes"] for row in entries.values()),
             "archive_sha256": sha(ARCHIVE), "archive_bytes": ARCHIVE.stat().st_size}
    verify_archive(ARCHIVE, value)
    MANIFEST.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "files": len(entries),
                      "source_bytes": value["source_total_bytes"],
                      "archive_bytes": value["archive_bytes"]}))


def verify(proof):
    manifest = json.loads((WORKER_ROOT / MANIFEST.name).read_text())
    archive = WORKER_ROOT / ARCHIVE.name
    host = socket.gethostname()
    if host not in WORKERS or proof.exists() or sha(archive) != manifest["archive_sha256"]:
        raise ValueError("Wrong worker or archive hash mismatch")
    verify_archive(archive, manifest)
    value = {"status": "WORKER_ARCHIVE_RESTORED_AND_VERIFIED", "worker": host,
             "worker_ip": WORKERS[host][0], "verified_utc": datetime.now(timezone.utc).isoformat(),
             "archive_path": str(archive), "archive_sha256": manifest["archive_sha256"],
             "manifest_sha256": sha(WORKER_ROOT / MANIFEST.name),
             "restored_files": manifest["file_count"], "restored_bytes": manifest["source_total_bytes"]}
    proof.parent.mkdir(parents=True, exist_ok=True)
    proof.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "worker": host,
                      "restored_files": value["restored_files"]}))


def release(proofs, output):
    if output.exists() or len(proofs) != 2:
        raise ValueError("Two independent proofs and new output required")
    manifest = json.loads(MANIFEST.read_text())
    if sha(ARCHIVE) != manifest["archive_sha256"]:
        raise ValueError("Master compressed source changed")
    verify_archive(ARCHIVE, manifest)
    values = [json.loads(path.read_text()) for path in proofs]
    if {row["worker"] for row in values} != set(WORKERS) or any(
            row["status"] != "WORKER_ARCHIVE_RESTORED_AND_VERIFIED" or
            row["archive_sha256"] != manifest["archive_sha256"] or
            row["manifest_sha256"] != sha(MANIFEST) or
            row["restored_files"] != manifest["file_count"] for row in values):
        raise ValueError("Independent archive proof incomplete")
    files = source_files()
    if set(files) != set(manifest["files"]):
        raise ValueError("Master duplicate file set changed")
    for relative, path in files.items():
        row = manifest["files"][relative]
        if path.stat().st_size != row["bytes"] or sha(path) != row["sha256"]:
            raise ValueError("Master source changed: " + relative)
    for relative_root in RELATIVE_ROOTS:
        check = subprocess.run(["lsof", "-nP", "+D", str(ROOT / relative_root)],
                               capture_output=True, text=True, timeout=90)
        if check.returncode not in (0, 1) or check.stdout.splitlines()[1:]:
            raise RuntimeError("Active source file handle")
    for path in files.values():
        path.unlink()
    for relative_root in RELATIVE_ROOTS:
        root = ROOT / relative_root
        for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_dir():
                path.rmdir()
        root.rmdir()
    value = {"status": "MASTER_DUPLICATE_RELEASED_AFTER_DUAL_RESTORE",
             "released_utc": datetime.now(timezone.utc).isoformat(),
             "source_manifest_sha256": sha(MANIFEST),
             "archive_sha256": manifest["archive_sha256"],
             "released_files": manifest["file_count"],
             "released_bytes": manifest["source_total_bytes"],
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
