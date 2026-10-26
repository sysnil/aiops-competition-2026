"""Release only a frozen, twice-verified Clean Gate v2 run."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from archive_batch import ALLOWED, digest, open_files


ARCHIVE_PREFIX = "/data/rca-archives/stage5-v3-clean-gate-v2-20261012/"


def validated_release_plan(manifest_path, proofs, restored_sample):
    manifest = json.loads(manifest_path.read_text())
    name = manifest["batch"]
    root = ALLOWED.get(name)
    if root is None or str(root) != manifest["source"] or not root.is_dir():
        raise ValueError("Source outside explicit release whitelist")
    expected_sha = digest(manifest_path)
    if len(proofs) != 2 or len({str(path) for path in proofs}) != 2:
        raise ValueError("Two independent verification records required")
    proof_values = [json.loads(path.read_text()) for path in proofs]
    for value in proof_values:
        if (value.get("status") != "ARCHIVE_VERIFIED" or
            value.get("batch") != name or
            value.get("source_manifest_sha256") != expected_sha or
            value.get("file_count") != manifest["file_count"] or
            value.get("total_bytes") != manifest["total_bytes"] or
            not value.get("archive_path", "").startswith(ARCHIVE_PREFIX)):
            raise ValueError("Archive proof does not match frozen source")
    if {"192.168.53.54", "192.168.53.55"} != {
            value.get("worker_ip") for value in proof_values}:
        raise ValueError("Proofs do not identify independent Worker2 and Worker3")
    sample = json.loads(restored_sample.read_text())
    if sample.get("status") != "RESTORE_TEST_PASS" or \
            sample.get("source_manifest_sha256") != expected_sha or \
            set(sample.get("worker_ips", [])) != {"192.168.53.54", "192.168.53.55"}:
        raise ValueError("Independent restore test missing")
    if open_files(root):
        raise ValueError("Active source file handle")
    expected = manifest["files"]
    actual = {str(path.relative_to(root)): path for path in root.rglob("*")
              if path.is_file()}
    if set(actual) != set(expected):
        raise ValueError("Source whitelist no longer matches")
    for relative, path in actual.items():
        row = expected[relative]
        meta = path.stat()
        if meta.st_size != row["bytes"] or meta.st_mtime_ns != row["mtime_ns"] or \
                digest(path) != row["sha256"]:
            raise ValueError("Source changed after copy: " + relative)
    return manifest, actual


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--proof", required=True, type=Path, action="append")
    parser.add_argument("--restore-test", required=True, type=Path)
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Release record exists")
    manifest, actual = validated_release_plan(args.manifest, args.proof,
                                               args.restore_test)
    pre_free = subprocess.check_output(["df", "-B1", "--output=avail", "/"],
                                       text=True).splitlines()[-1].strip()
    deleted = 0
    if args.release:
        for relative in sorted(actual):
            actual[relative].unlink()
            deleted += 1
    value = {"status": "SOURCE_RELEASE_COMPLETE" if args.release else
        "SOURCE_RELEASE_DRY_RUN_PASS", "batch": manifest["batch"],
        "source": manifest["source"], "source_manifest_sha256": digest(args.manifest),
        "released_files": deleted, "whitelisted_files": len(actual),
        "whitelisted_bytes": manifest["total_bytes"],
        "master_free_bytes_before": int(pre_free),
        "master_free_bytes_after": int(subprocess.check_output([
            "df", "-B1", "--output=avail", "/"], text=True).splitlines()[-1]),
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "worker_archive_paths": [json.loads(path.read_text())["archive_path"]
                                 for path in args.proof]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: value[key] for key in
        ("status", "batch", "released_files", "master_free_bytes_after")}))


if __name__ == "__main__":
    main()
