#!/usr/bin/env python3
"""Dual-worker SHA and restore proof for the new-disk live public capture."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import subprocess


RUN_ID = "S5V3FR-DISK-CANARY-20261013"
MASTER = Path("/data/stage5-v3/fast-ready/capture") / RUN_ID
WORKER = Path("/data/rca-archives/stage5-v3-fast-ready-20261013") / (
    RUN_ID + "-capture")
WORKERS = {"k8s-worker2": "192.168.53.54", "k8s-worker3": "192.168.53.55"}


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(root):
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Capture root absent or symlink")
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Capture contains symlink")
        if path.is_file():
            files[str(path.relative_to(root))] = {"bytes": path.stat().st_size,
                "sha256": sha(path)}
    required = {"capture-manifest.json", "kubernetes/pods.json"}
    if not required <= set(files) or not all(any(name.startswith(prefix) for name in files)
        for prefix in ("metrics/", "logs/", "trace-storage-pages/")):
        raise ValueError("Capture has incomplete source families")
    manifest = json.loads((root / "capture-manifest.json").read_text())
    if manifest["phase"] != "during" or manifest[
            "trace_source"] != "JAEGER_OPENSEARCH_STORAGE" or not manifest[
            "sha256"]:
        raise ValueError("Live capture manifest incomplete")
    for name, digest in manifest["sha256"].items():
        if name not in files or files[name]["sha256"] != digest:
            raise ValueError("Capture manifest file SHA mismatch: " + name)
    return files


def sample_restore(root):
    metrics = json.loads(sorted((root / "metrics").glob("*.json"))[0].read_text())
    logs = json.loads(sorted((root / "logs").glob("*.json"))[0].read_text())
    spans = json.loads(sorted((root / "trace-storage-pages").glob("*.json"))[0].read_text())
    pods = json.loads((root / "kubernetes/pods.json").read_text())
    if (metrics.get("status") != "success" or "hits" not in logs or
            "hits" not in spans or not pods.get("items")):
        raise ValueError("One of four source families failed restore parsing")
    return {"metric_series": len(metrics["data"]["result"]),
        "log_page_hits": len(logs["hits"]["hits"]),
        "trace_page_hits": len(spans["hits"]["hits"]),
        "kubernetes_pods": len(pods["items"])}


def save(path, value):
    if path.exists():
        raise ValueError("Refusing overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def create(manifest_path):
    if socket.gethostname() != "k8s-master":
        raise ValueError("Master-only source inventory")
    from data_disk_guard import assert_ready
    guard = assert_ready(MASTER)
    handles = subprocess.run(["lsof", "-nP", "+D", str(MASTER)],
        capture_output=True, text=True, timeout=90)
    if handles.returncode not in (0, 1) or handles.stdout.splitlines()[1:]:
        raise RuntimeError("Capture has active writer")
    files = inventory(MASTER)
    value = {"status": "DATA_DISK_LIVE_CAPTURE_FROZEN",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(MASTER), "files": files, "file_count": len(files),
        "source_bytes": sum(row["bytes"] for row in files.values()),
        "sample_restore": sample_restore(MASTER),
        "data_disk_guard": guard, "fault_injected": False}
    save(manifest_path, value)
    print(json.dumps({"status": value["status"],
        "files": value["file_count"], "bytes": value["source_bytes"]}))


def verify(manifest_path, output):
    if socket.gethostname() not in WORKERS:
        raise ValueError("Unknown Worker")
    expected = json.loads(manifest_path.read_text())
    if expected["source"] != str(MASTER):
        raise ValueError("Wrong Master source path")
    files = inventory(WORKER)
    restored = sample_restore(WORKER)
    if files != expected["files"] or restored != expected["sample_restore"]:
        raise ValueError("Worker SHA or restored source sample differs")
    value = {"status": "DATA_DISK_LIVE_CAPTURE_ARCHIVE_VERIFIED",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "worker": socket.gethostname(), "worker_ip": WORKERS[socket.gethostname()],
        "source_manifest_sha256": sha(manifest_path), "archive_path": str(WORKER),
        "file_count": len(files), "source_bytes": expected["source_bytes"],
        "sample_restore": restored}
    save(output, value)
    print(json.dumps({"status": value["status"], "worker": value["worker"]}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("create", "verify"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "create":
        create(args.manifest)
    elif args.output:
        verify(args.manifest, args.output)
    else:
        raise ValueError("Worker proof output required")


if __name__ == "__main__":
    main()
