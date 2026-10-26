#!/usr/bin/env python3
"""Keep new Stage5-v3 raw data on the UUID-mounted disk, fail closed."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess


MOUNT = Path("/data/stage5-v3")
UUID = "a35051a2-3d09-4b83-bbc0-3bb055e8a4f3"
FS_TYPE = "ext4"
DISK = "/dev/sdb1"
DATA_MIN_FREE_BYTES = 64 * 1024 ** 3
DEFAULT_PLANNED_BYTES = 5 * 1024 ** 3
POLICY = Path("/root/stage5-v3-clean-gate-v2/design/global-safety-policy-draft.json")
LAYOUT = Path("/root/stage5-v3-fast-ready/results/data-layout.json")
DIRS = ("runs", "raw-metrics", "raw-logs", "raw-traces", "capture",
        "snapshots", "tmp-archive", "tmp")


def findmnt(field):
    value = subprocess.check_output(["findmnt", "-n", "-o", field, "-T",
        str(MOUNT)], text=True, timeout=10).strip()
    if not value:
        raise RuntimeError("Mounted filesystem field absent: " + field)
    return value


def assert_ready(path, planned_bytes=DEFAULT_PLANNED_BYTES):
    if not MOUNT.is_dir() or MOUNT.is_symlink():
        raise RuntimeError("Stage5-v3 data mountpoint absent or symlink")
    source, fs, uuid, options = [findmnt(field) for field in (
        "SOURCE", "FSTYPE", "UUID", "OPTIONS")]
    if (source != DISK or fs != FS_TYPE or uuid != UUID or
            not {"nodev", "nosuid", "noexec"} <= set(options.split(",")) or
            os.stat(MOUNT).st_dev == os.stat("/").st_dev):
        raise RuntimeError("Stage5-v3 data mount is missing or is the wrong disk")
    resolved = path.resolve(strict=False)
    if not path.is_absolute() or not resolved.is_relative_to(MOUNT) or resolved == MOUNT:
        raise ValueError("Raw output must remain beneath the approved data mount")
    relative = path.absolute().relative_to(MOUNT)
    current = MOUNT
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Raw output traverses a symlink")
    parent = path.parent
    while not parent.exists() and parent != MOUNT:
        parent = parent.parent
    if os.stat(parent).st_dev != os.stat(MOUNT).st_dev:
        raise RuntimeError("Nearest existing raw-output parent is not on data filesystem")
    root_free = shutil.disk_usage("/").free
    data = shutil.disk_usage(MOUNT)
    root_floor = json.loads(POLICY.read_text())["minimum_free_bytes_for_planned_runs"]
    if (root_free < root_floor or data.total < 300_000_000_000 or
            data.free - planned_bytes < DATA_MIN_FREE_BYTES):
        raise RuntimeError("Root or data disk safety budget insufficient")
    return {"status": "STAGE5V3_DATA_DISK_GUARD_PASS",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": source, "filesystem": fs, "uuid": uuid,
        "mountpoint": str(MOUNT), "output_path": str(path),
        "root_device": os.stat("/").st_dev,
        "data_device": os.stat(MOUNT).st_dev,
        "root_free_bytes": root_free, "root_minimum_free_bytes": root_floor,
        "data_total_bytes": data.total, "data_free_bytes": data.free,
        "data_minimum_free_bytes": DATA_MIN_FREE_BYTES,
        "reserved_planned_output_bytes": planned_bytes,
        "data_output_is_not_root_filesystem": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--path", type=Path)
    parser.add_argument("--planned-bytes", type=int, default=DEFAULT_PLANNED_BYTES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.planned_bytes < 0 or args.planned_bytes > 100 * 1024 ** 3:
        raise ValueError("Invalid planned output budget")
    target = args.path or MOUNT / "fast-ready/runs/next"
    result = assert_ready(target, args.planned_bytes)
    if args.prepare:
        if LAYOUT.exists():
            raise ValueError("Refusing layout overwrite")
        base = MOUNT / "fast-ready"
        for name in DIRS:
            directory = base / name
            directory.mkdir(parents=True, exist_ok=True, mode=0o750)
            os.chmod(directory, 0o750)
            if directory.is_symlink() or os.stat(directory).st_dev != os.stat(MOUNT).st_dev:
                raise RuntimeError("Created data directory escaped approved disk")
        result["data_directories"] = [str(base / name) for name in DIRS]
        result["layout_created"] = True
        LAYOUT.write_text(json.dumps(result, indent=2) + "\n")
    if args.output:
        if args.output.exists():
            raise ValueError("Refusing guard output overwrite")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in (
        "status", "mountpoint", "data_free_bytes", "root_free_bytes")}))


if __name__ == "__main__":
    main()
