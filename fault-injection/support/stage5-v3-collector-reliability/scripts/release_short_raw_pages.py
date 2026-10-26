#!/usr/bin/env python3
"""Release only verified Master duplicate raw pages after dual archive restore."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--proof", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args()
    if args.output.exists() or len(args.proof) != 2:
        raise ValueError("New output and two archive proofs required")
    manifest = json.loads(args.manifest.read_text())
    root = Path(manifest["source"])
    if (manifest["status"] != "SOURCE_INVENTORY_FROZEN" or root !=
            Path("/root/stage5-v3-collector-reliability/runs") / manifest["name"] or
            manifest["name"] not in {"CR-REPAIR-CANARY-1-20261012",
                "CR-REPAIR-CANARY-2-20261012"}):
        raise ValueError("Run outside exact release whitelist")
    digest = sha(args.manifest)
    proofs = [json.loads(path.read_text()) for path in args.proof]
    if ({proof.get("worker_ip") for proof in proofs} != {"192.168.53.54", "192.168.53.55"}
            or any(proof.get("status") != "ARCHIVE_VERIFIED_AND_RESTORED" or
                   proof.get("source_manifest_sha256") != digest or
                   proof.get("file_count") != manifest["file_count"] or
                   len(proof.get("restored_samples", {})) != 3 for proof in proofs)):
        raise ValueError("Dual archive or restore verification incomplete")
    opened = subprocess.run(["lsof", "-nP", "+D", str(root)], capture_output=True,
                            text=True, timeout=90)
    if opened.returncode not in (0, 1) or opened.stdout.splitlines()[1:]:
        raise RuntimeError("Source still has open files")
    actual = {str(path.relative_to(root)): path for path in root.rglob("*") if path.is_file()}
    if set(actual) != set(manifest["files"]):
        raise ValueError("Master source changed since inventory")
    for relative, path in actual.items():
        expected = manifest["files"][relative]
        if path.stat().st_size != expected["bytes"] or sha(path) != expected["sha256"]:
            raise ValueError("Master source hash drift: " + relative)
    pages = {relative: path for relative, path in actual.items()
             if (relative.startswith("trace-audit/raw-pages/") or
                 relative.startswith("late-audit/raw-pages/")) and relative.endswith(".json")}
    if not pages:
        raise ValueError("No registered raw pages to release")
    free_before = int(subprocess.check_output(["df", "-B1", "--output=avail", "/"],
                                              text=True).splitlines()[-1])
    if args.release:
        for path in pages.values():
            path.unlink()
    free_after = int(subprocess.check_output(["df", "-B1", "--output=avail", "/"],
                                             text=True).splitlines()[-1])
    value = {"status": "RAW_PAGE_DUPLICATES_RELEASED" if args.release else "RAW_PAGE_RELEASE_DRY_RUN_PASS",
             "at_utc": datetime.now(timezone.utc).isoformat(), "run": manifest["name"],
             "source_manifest_sha256": digest, "released_count": len(pages) if args.release else 0,
             "released_bytes": sum(manifest["files"][key]["bytes"] for key in pages) if args.release else 0,
             "master_free_before": free_before, "master_free_after": free_after,
             "worker_archives": [proof["archive"] for proof in proofs]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps(value))


if __name__ == "__main__":
    main()
