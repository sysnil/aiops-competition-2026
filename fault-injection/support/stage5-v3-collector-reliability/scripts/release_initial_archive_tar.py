#!/usr/bin/env python3
"""Release Master compressed duplicate only after two verified Worker copies."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path("/root/stage5-v3-collector-reliability")
ARCHIVE = ROOT / "archive/initial-snapshot.tar.gz"
MANIFEST = ROOT / "archive/initial-snapshot-source.json"
PROOFS = [ROOT / "archive/initial-worker2-proof.json", ROOT / "archive/initial-worker3-proof.json"]
OUTPUT = ROOT / "archive/initial-master-tar-release.json"
WORKERS = {"192.168.53.54": "/tmp/stage5v3-w2-archive.sock",
           "192.168.53.55": "/tmp/stage5v3-w3-archive.sock"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUTPUT.exists() or not ARCHIVE.is_file():
        raise ValueError("Exact duplicate or new release record absent")
    manifest = json.loads(MANIFEST.read_text())
    expected = manifest["archive_sha256"]
    if sha(ARCHIVE) != expected:
        raise ValueError("Master archive hash mismatch")
    proofs = [json.loads(path.read_text()) for path in PROOFS]
    if {row["worker_ip"] for row in proofs} != set(WORKERS) or any(
            row["status"] != "WORKER_ARCHIVE_RESTORED_AND_VERIFIED" or
            row["archive_sha256"] != expected or row["manifest_sha256"] != sha(MANIFEST)
            for row in proofs):
        raise ValueError("Two independent restore proofs required")
    for row in proofs:
        address = row["worker_ip"]
        output = subprocess.check_output(["ssh", "-o", "BatchMode=yes",
            "-o", "ControlPath=" + WORKERS[address], "root@" + address,
            "sha256sum", row["archive_path"]], text=True, timeout=20)
        if output.split()[0] != expected:
            raise ValueError("Worker archive changed after verification")
    released_bytes = ARCHIVE.stat().st_size
    ARCHIVE.unlink()
    result = {"status": "MASTER_COMPRESSED_DUPLICATE_RELEASED",
              "at_utc": datetime.now(timezone.utc).isoformat(),
              "released_path": str(ARCHIVE), "released_bytes": released_bytes,
              "archive_sha256": expected, "manifest_sha256": sha(MANIFEST),
              "worker_archives": [row["archive_path"] for row in proofs]}
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "released_bytes": released_bytes}))


if __name__ == "__main__":
    main()
