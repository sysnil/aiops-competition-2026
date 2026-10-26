"""Apply existing Public Case read permissions to newly imported SF-013..017."""

from __future__ import annotations

import argparse
import grp
import os
from pathlib import Path


ROOT = Path("/data/aiops-contest/public")
FILES = {"metrics.parquet", "logs.parquet", "traces.parquet", "trace_index.parquet"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    group = grp.getgrnam("aiops-api").gr_gid
    checked = []
    for number in range(13, 18):
        case_id = f"SF-{number:03d}"
        directory = ROOT / "cases" / case_id / "public-v1"
        manifest = ROOT / "manifests" / f"{case_id}.json"
        if (directory.is_symlink() or not directory.is_dir() or manifest.is_symlink() or
                not manifest.is_file() or {p.name for p in directory.iterdir()} != FILES or
                any(p.is_symlink() or not p.is_file() for p in directory.iterdir())):
            raise ValueError(f"unexpected published Case layout: {case_id}")
        checked.append(case_id)
    if args.apply:
        for case_id in checked:
            directory = ROOT / "cases" / case_id / "public-v1"
            manifest = ROOT / "manifests" / f"{case_id}.json"
            os.chown(directory, 0, group)
            os.chmod(directory, 0o750)
            for file in directory.iterdir():
                os.chown(file, 0, group)
                os.chmod(file, 0o440)
            os.chown(manifest, 0, group)
            os.chmod(manifest, 0o440)
    print({"status": "APPLIED" if args.apply else "LAYOUT_VALIDATED", "cases": checked})


if __name__ == "__main__":
    main()
