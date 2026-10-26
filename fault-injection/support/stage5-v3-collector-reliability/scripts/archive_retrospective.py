#!/usr/bin/env python3
"""Dual-worker proof for delayed, non-compliant OpenSearch Trace retrieval."""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import socket


ROOT = Path("/data/rca-archives/stage5-v3-collector-reliability-20261012")
CAPTURE = ROOT / "continuous-retrospective"
MANIFEST = ROOT / "continuous-retrospective-source-manifest.json"
WORKERS = {"k8s-worker2": "192.168.53.54", "k8s-worker3": "192.168.53.55"}


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory():
    if CAPTURE.is_symlink() or not CAPTURE.is_dir():
        raise ValueError("Capture directory absent or symlink")
    files = {}
    for path in sorted(CAPTURE.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Unexpected capture entry")
        files[path.name] = {"bytes": path.stat().st_size, "sha256": sha(path)}
    capture = json.loads((CAPTURE / "capture-manifest.json").read_text())
    pages = capture["source"]["raw_pages"]
    if (capture["status"] != "TRACE_CAPTURE_COMPLETE" or
            capture["start_after_end_seconds"] <= 240 or
            capture["source"]["actual_hits"] != capture["source"]["expected_hits"] or
            set(files) != {"capture-manifest.json", *pages} or
            any(files[name] != {"bytes": info["bytes"], "sha256": info["sha256"]}
                for name, info in pages.items())):
        raise ValueError("Retrospective source is not a complete late capture")
    return files, capture


def restore_page(name, expected_hits):
    with gzip.open(CAPTURE / name, "rt", encoding="utf-8") as stream:
        page = json.load(stream)
    count = len(page["hits"]["hits"])
    if count != expected_hits or not all("traceID" in row["_source"] and
                                        "spanID" in row["_source"]
                                        for row in page["hits"]["hits"]):
        raise ValueError("Page restore/identity mismatch")
    return count


def save(path, value):
    if path.exists():
        raise ValueError("Refusing overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def create():
    if socket.gethostname() != "k8s-worker2":
        raise ValueError("Source inventory must run on Worker2")
    files, capture = inventory()
    pages = capture["source"]["raw_pages"]
    names = sorted(pages)
    restored = {name: restore_page(name, pages[name]["hit_count"])
                for name in (names[0], names[-1])}
    value = {"status": "RETROSPECTIVE_SOURCE_VERIFIED_NOT_TIMELY",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "capture_dir": str(CAPTURE), "file_count": len(files),
        "source_bytes": sum(row["bytes"] for row in files.values()),
        "files": files, "first_last_page_restore": restored,
        "read_started_utc": capture["source"]["read_started_utc"],
        "start_after_end_seconds": capture["start_after_end_seconds"],
        "formal_plus_120_or_240_satisfied": False}
    save(MANIFEST, value)
    print(json.dumps({"status": value["status"], "files": len(files),
                      "bytes": value["source_bytes"]}))


def verify(output):
    if socket.gethostname() not in WORKERS:
        raise ValueError("Unknown Worker")
    source = json.loads(MANIFEST.read_text())
    files, capture = inventory()
    if (files != source["files"] or
            capture["source"]["read_started_utc"] != source["read_started_utc"]):
        raise ValueError("Retrospective archive differs from source")
    pages = capture["source"]["raw_pages"]
    names = sorted(pages)
    restored = {name: restore_page(name, pages[name]["hit_count"])
                for name in (names[0], names[-1])}
    if restored != source["first_last_page_restore"]:
        raise ValueError("Page restoration differs from source")
    value = {"status": "RETROSPECTIVE_ARCHIVE_VERIFIED_AND_RESTORED",
        "worker": socket.gethostname(), "worker_ip": WORKERS[socket.gethostname()],
        "source_manifest_sha256": sha(MANIFEST), "capture_dir": str(CAPTURE),
        "file_count": len(files), "source_bytes": source["source_bytes"],
        "first_last_page_restore": restored,
        "formal_plus_120_or_240_satisfied": False,
        "verified_utc": datetime.now(timezone.utc).isoformat()}
    save(output, value)
    print(json.dumps({"status": value["status"], "worker": value["worker"]}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("create", "verify"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "create":
        create()
    elif args.output:
        verify(args.output)
    else:
        raise ValueError("Verification proof path required")


if __name__ == "__main__":
    main()
