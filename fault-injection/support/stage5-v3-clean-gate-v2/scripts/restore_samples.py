"""Restore representative raw evidence from both Worker archives."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from archive_batch import digest


WORKERS = (("192.168.53.54", "/tmp/stage5v3-w2-archive.sock"),
           ("192.168.53.55", "/tmp/stage5v3-w3-archive.sock"))
BASE = "/data/rca-archives/stage5-v3-clean-gate-v2-20261012"


def select(manifest):
    chosen = {}
    for kind in ("metrics", "logs", "traces", "observations"):
        candidates = [(name, info) for name, info in manifest["files"].items()
                      if "/" + kind + "/" in name or kind in name]
        candidates = [(name, info) for name, info in candidates
                      if name.endswith((".json", ".jsonl")) and
                      0 < info["bytes"] <= 1024 * 1024]
        if candidates:
            chosen[kind] = sorted(candidates)[0][0]
    if not chosen:
        candidates = [(name, info) for name, info in manifest["files"].items()
                      if name.endswith((".json", ".jsonl")) and
                      0 < info["bytes"] <= 1024 * 1024]
        if not candidates:
            raise RuntimeError("No parseable real evidence sample")
        chosen["other"] = sorted(candidates)[0][0]
    return chosen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--restore-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.restore_root.exists():
        raise ValueError("Restore output already exists")
    manifest = json.loads(args.manifest.read_text())
    chosen = select(manifest)
    samples = []
    for ip, socket in WORKERS:
        for category, relative in chosen.items():
            destination = args.restore_root / ip / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            remote = f"root@{ip}:{BASE}/{manifest['batch']}/{relative}"
            subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o",
                            "ControlPath=" + socket, remote, str(destination)],
                           check=True, timeout=90)
            if digest(destination) != manifest["files"][relative]["sha256"]:
                raise RuntimeError("Restored sample SHA mismatch")
            with destination.open() as stream:
                content = json.loads(stream.readline()) if relative.endswith(
                    ".jsonl") else json.load(stream)
            samples.append({"worker_ip": ip, "category": category,
                "relative_path": relative, "bytes": destination.stat().st_size,
                "sha256": digest(destination), "json_type": type(content).__name__})
    value = {"status": "RESTORE_TEST_PASS", "batch": manifest["batch"],
        "source_manifest_sha256": digest(args.manifest),
        "worker_ips": [ip for ip, _ in WORKERS], "samples": samples,
        "at_utc": datetime.now(timezone.utc).isoformat()}
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": value["status"], "samples": len(samples)}))


if __name__ == "__main__":
    main()
