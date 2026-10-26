"""Inventory and verify the one registered UID Canary run tree."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess


ALLOWED = {
    "uid-canary": Path("/root/stage5-v3-observability-diagnostics/runs/OD-UID-CANARY-20261012"),
}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def open_files(root):
    result = subprocess.run(["lsof", "-nP", "+D", str(root)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode not in (0, 1):
        raise RuntimeError("lsof inspection failed: " + result.stderr[-300:])
    return result.stdout.splitlines()[1:]


def inventory(name, root, output):
    if output.exists() or root != ALLOWED.get(name) or not root.is_dir():
        raise ValueError("Unregistered or existing source/manifest")
    if open_files(root):
        raise RuntimeError("Source has open files")
    files, directories, links = {}, {}, {}
    root_dev = root.stat().st_dev
    for current, dirs, filenames in os.walk(root, followlinks=False):
        current = Path(current)
        for dirname in dirs:
            path = current / dirname
            row = path.lstat()
            relative = str(path.relative_to(root))
            if stat.S_ISLNK(row.st_mode):
                links[relative] = os.readlink(path)
            elif not stat.S_ISDIR(row.st_mode) or row.st_dev != root_dev:
                raise RuntimeError("Unexpected mounted or special path: " + str(path))
            else:
                directories[relative] = {"mode": stat.S_IMODE(row.st_mode),
                    "uid": row.st_uid, "gid": row.st_gid,
                    "mtime_ns": row.st_mtime_ns}
        for filename in filenames:
            path = current / filename
            row = path.lstat()
            relative = str(path.relative_to(root))
            if stat.S_ISLNK(row.st_mode):
                links[relative] = os.readlink(path)
                continue
            if not stat.S_ISREG(row.st_mode) or row.st_dev != root_dev:
                raise RuntimeError("Special file or crossed filesystem: " + str(path))
            if row.st_nlink != 1:
                raise RuntimeError("External hardlink risk: " + str(path))
            before = (row.st_size, row.st_mtime_ns, row.st_ctime_ns)
            sha = digest(path)
            after = path.stat()
            if before != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise RuntimeError("File changed while hashing: " + str(path))
            files[relative] = {"bytes": row.st_size, "sha256": sha,
                "mode": stat.S_IMODE(row.st_mode), "uid": row.st_uid,
                "gid": row.st_gid, "mtime_ns": row.st_mtime_ns}
    if links:
        raise RuntimeError("Symlink relation requires manual review: " + str(links)[:500])
    if open_files(root):
        raise RuntimeError("Source became active during inventory")
    value = {"status": "SOURCE_INVENTORY_FROZEN", "batch": name,
        "source": str(root), "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": files, "directories": directories, "symlinks": links,
        "file_count": len(files), "total_bytes": sum(row["bytes"] for row in files.values()),
        "source_running_writers": 0}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"status": value["status"], "batch": name,
                      "file_count": value["file_count"],
                      "total_bytes": value["total_bytes"],
                      "manifest_sha256": digest(output)}))


def verify(manifest_path, destination):
    manifest = json.loads(manifest_path.read_text())
    expected = manifest["files"]
    actual = {}
    actual_dirs = {}
    root_dev = destination.stat().st_dev
    for current, dirs, filenames in os.walk(destination, followlinks=False):
        current = Path(current)
        for dirname in dirs:
            path = current / dirname
            if path.is_symlink() or path.stat().st_dev != root_dev:
                raise RuntimeError("Unexpected directory in destination")
            if dirname != ".rsync-partial":
                actual_dirs[str(path.relative_to(destination))] = path
        for filename in filenames:
            path = current / filename
            relative = str(path.relative_to(destination))
            if relative.startswith(".rsync-partial/"):
                raise RuntimeError("Unfinished rsync partial file")
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("Unexpected destination file type")
            actual[relative] = path
    if set(actual) != set(expected):
        raise RuntimeError("Relative-path set mismatch: missing=%d extra=%d" %
                           (len(set(expected) - set(actual)),
                            len(set(actual) - set(expected))))
    if set(actual_dirs) != set(manifest["directories"]):
        raise RuntimeError("Destination directory set differs from source")
    for relative, path in actual_dirs.items():
        row = manifest["directories"][relative]
        meta = path.stat()
        if (stat.S_IMODE(meta.st_mode) != row["mode"] or
            meta.st_uid != row["uid"] or meta.st_gid != row["gid"] or
            meta.st_mtime_ns != row["mtime_ns"]):
            raise RuntimeError("Directory attributes differ: " + relative)
    for relative, path in actual.items():
        row = expected[relative]
        meta = path.stat()
        if (meta.st_size != row["bytes"] or digest(path) != row["sha256"] or
            stat.S_IMODE(meta.st_mode) != row["mode"] or
            meta.st_uid != row["uid"] or meta.st_gid != row["gid"] or
            meta.st_mtime_ns != row["mtime_ns"]):
            raise RuntimeError("Content or attributes differ: " + relative)
    worker_ips = {"k8s-worker2": "192.168.53.54",
                  "k8s-worker3": "192.168.53.55"}
    host = socket.gethostname()
    if host not in worker_ips:
        raise RuntimeError("Archive proof must be issued on registered Worker")
    return {"status": "ARCHIVE_VERIFIED", "batch": manifest["batch"],
            "worker_hostname": host, "worker_ip": worker_ips[host],
            "archive_path": str(destination), "file_count": len(actual),
            "total_bytes": sum(row["bytes"] for row in expected.values()),
            "source_manifest_sha256": digest(manifest_path),
            "verified_at_utc": datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("inventory", "verify"))
    parser.add_argument("--batch", choices=sorted(ALLOWED))
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "inventory":
        inventory(args.batch, ALLOWED[args.batch], args.manifest)
        return
    if args.destination is None or args.output is None or args.output.exists():
        raise ValueError("Verification needs new output and destination")
    value = verify(args.manifest, args.destination)
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps(value))


if __name__ == "__main__":
    main()
