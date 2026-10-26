"""Restore one historical file to its original Master path after SHA validation."""

import argparse
import json
import os
from pathlib import Path
import subprocess

from archive_batch import ALLOWED, digest


WORKERS = {
    "worker2": ("192.168.53.54", "/tmp/stage5v3-w2-archive.sock"),
    "worker3": ("192.168.53.55", "/tmp/stage5v3-w3-archive.sock"),
}


def validate_path(relative):
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(part in ("..", ".")
                                                 for part in path.parts):
        raise ValueError("Unsafe relative path")
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--relative", required=True)
    parser.add_argument("--worker", choices=sorted(WORKERS), required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    proof = json.loads(args.proof.read_text())
    relative = validate_path(args.relative)
    key = str(relative)
    if key not in manifest["files"] or manifest["source"] != str(
            ALLOWED.get(manifest["batch"])) or proof.get("status") != \
            "ARCHIVE_VERIFIED" or proof.get("batch") != manifest["batch"] or \
            proof.get("source_manifest_sha256") != digest(args.manifest):
        raise ValueError("Unverified archive mapping")
    ip, socket = WORKERS[args.worker]
    if proof.get("worker_ip") != ip:
        raise ValueError("Proof belongs to different Worker")
    destination = ALLOWED[manifest["batch"]] / relative
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Refusing to overwrite Master path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = "root@" + ip + ":" + proof["archive_path"] + "/" + key
    subprocess.run(["rsync", "-a", "--ignore-existing", "-e",
                    "ssh -S " + socket + " -o BatchMode=yes", source,
                    str(destination)], check=True, timeout=300)
    meta = manifest["files"][key]
    if destination.stat().st_size != meta["bytes"] or \
            digest(destination) != meta["sha256"]:
        raise RuntimeError("Restored file hash mismatch; inspect output before use")
    os.chown(destination, meta["uid"], meta["gid"])
    os.chmod(destination, meta["mode"])
    os.utime(destination, ns=(meta["mtime_ns"], meta["mtime_ns"]))
    print(json.dumps({"status": "RESTORED_AND_VERIFIED", "master_path":
        str(destination), "sha256": meta["sha256"], "worker_ip": ip}))


if __name__ == "__main__":
    main()
