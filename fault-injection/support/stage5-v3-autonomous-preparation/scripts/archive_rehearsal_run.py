#!/usr/bin/env python3
"""Losslessly archive only this rehearsal's finished runs with per-file SHA-256."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tarfile


MASTER_ROOT = Path("/root/stage5-v3-autonomous-preparation")


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while data := stream.read(1024 * 1024):
            digest.update(data)
    return digest.hexdigest()


def paths(root, kind, run_id):
    if not run_id.startswith("S5V3AP-") or "/" in run_id or ".." in run_id:
        raise ValueError("Only this independent rehearsal's IDs can be archived")
    if kind not in ("runs", "attempts"):
        raise ValueError("Only independent run or attempt archives are eligible")
    source = root / kind / run_id
    archive = root / "archives" / (run_id + ".tar.gz")
    manifest = root / "archives" / (run_id + ".manifest.json")
    return source, archive, manifest


def source_files(source):
    if not source.is_dir() or source.is_symlink():
        raise ValueError("Archived source must be one existing real directory")
    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files or any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("Empty archive or symlink is not eligible")
    return files


def verify_archive(archive, manifest):
    value = json.loads(manifest.read_text())
    if value.get("status") != "LOSSLESS_VERIFIED" or sha(archive) != value["archive_sha256"]:
        raise ValueError("Compressed archive hash mismatch")
    remaining = dict(value["files"])
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if not member.isfile() or member.name not in remaining:
                raise ValueError("Archive contains an unexpected or unsafe member")
            expected = remaining.pop(member.name)
            if member.size != expected["bytes"]:
                raise ValueError("Archived source size changed")
            content = tar.extractfile(member)
            digest = hashlib.sha256()
            while data := content.read(1024 * 1024):
                digest.update(data)
            if digest.hexdigest() != expected["sha256"]:
                raise ValueError("Archived source content hash changed")
    if remaining:
        raise ValueError("Archive is missing registered source files")
    return value


def archive_run(source, archive, manifest, kind, run_id):
    if archive.exists() or manifest.exists():
        raise ValueError("Refusing to overwrite an existing compressed archive")
    files = source_files(source)
    if not (source / "result.json").is_file() and not (source / "failure.json").is_file():
        raise ValueError("Attempt or clean run has no terminal record")
    registered = {str(Path(run_id) / path.relative_to(source)): {
        "bytes": path.stat().st_size, "sha256": sha(path)} for path in files}
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "x:gz", compresslevel=3) as tar:
        for path in files:
            tar.add(path, arcname=str(Path(run_id) / path.relative_to(source)),
                    recursive=False)
    value = {"status": "LOSSLESS_VERIFIED", "created_at_utc":
        datetime.now(timezone.utc).isoformat(), "kind": kind, "run_id": run_id,
        "source_path": str(source), "archive_path": str(archive),
        "archive_sha256": sha(archive), "files": registered,
        "source_pruned_at_archive_creation": False, "restorable": True}
    pending = manifest.with_suffix(".pending")
    with pending.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    verify_archive(archive, pending)
    pending.rename(manifest)
    return value


def prune_verified_source(source, archive, manifest):
    value = verify_archive(archive, manifest)
    files = source_files(source)
    current = {str(Path(value["run_id"]) / path.relative_to(source)): {
        "bytes": path.stat().st_size, "sha256": sha(path)} for path in files}
    if current != value["files"]:
        raise ValueError("Source changed since the verified archive was created")
    for path in files:
        path.unlink()
    for directory in sorted((path for path in source.rglob("*") if path.is_dir()),
                            key=lambda item: len(item.parts), reverse=True):
        directory.rmdir()
    source.rmdir()
    # The original manifest remains immutable; this new receipt documents pruning.
    receipt = manifest.with_name(manifest.name.replace(".manifest.json", ".pruned.json"))
    with receipt.open("x") as stream:
        json.dump({"status": "SOURCE_PRUNED_AFTER_FULL_ARCHIVE_VERIFICATION",
                   "run_id": value["run_id"], "archive_sha256": value["archive_sha256"],
                   "manifest_sha256": sha(manifest), "at_utc":
                   datetime.now(timezone.utc).isoformat()}, stream, indent=2)
        stream.write("\n")
    return receipt


def restore(archive, manifest, target):
    value = verify_archive(archive, manifest)
    if target.exists():
        raise ValueError("Restore target already exists")
    target.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            member_path = Path(member.name)
            if (member_path.is_absolute() or ".." in member_path.parts or
                    not member_path.parts or member_path.parts[0] != value["run_id"]):
                raise ValueError("Archive member path escapes the registered ID")
            path = target / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                source = tar.extractfile(member)
                while data := source.read(1024 * 1024):
                    output.write(data)
    for name, evidence in value["files"].items():
        if sha(target / name) != evidence["sha256"]:
            raise ValueError("Restored file differs from original evidence")
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=MASTER_ROOT)
    parser.add_argument("--synthetic-test", action="store_true")
    parser.add_argument("--kind", choices=("runs", "attempts"), required=True)
    parser.add_argument("--run-id", required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--verify-only", action="store_true")
    action.add_argument("--prune-source", action="store_true")
    action.add_argument("--restore-to", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if root != MASTER_ROOT and not args.synthetic_test:
        raise ValueError("An alternate archive root is allowed only for tests")
    source, archive, manifest = paths(root, args.kind, args.run_id)
    if args.verify_only:
        value = verify_archive(archive, manifest)
    elif args.prune_source:
        receipt = prune_verified_source(source, archive, manifest)
        value = {"status": "SOURCE_PRUNED_AFTER_FULL_ARCHIVE_VERIFICATION",
                 "receipt": str(receipt)}
    elif args.restore_to:
        value = restore(archive, manifest, args.restore_to.resolve())
    else:
        value = archive_run(source, archive, manifest, args.kind, args.run_id)
    print(json.dumps({"status": value["status"], "run_id": args.run_id,
                      "archive": str(archive)}))


if __name__ == "__main__":
    main()
