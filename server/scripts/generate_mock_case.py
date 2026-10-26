#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from case_utils import manifest_hash, sha256_file, validate_case_directory


CASE_ID = "MOCK-SF-001"
VERSION = "mock-v2"
SERVICES = ("frontend", "checkout", "payment", "database")
PODS = {service: f"{service}-mock-0" for service in SERVICES}
NODES = {"frontend": "mock-node-a", "checkout": "mock-node-a", "payment": "mock-node-b", "database": "mock-node-b"}


def trace_id(minute: int) -> str:
    return f"{minute + 1:032x}"


def build_metrics(start: datetime) -> pd.DataFrame:
    rng = np.random.default_rng(20261012)
    rows = []
    counters = {service: 10000 for service in SERVICES}
    for minute in range(60):
        timestamp = start + timedelta(minutes=minute)
        for service in SERVICES:
            anomaly = service == "checkout" and 30 <= minute <= 38
            counters[service] += int(100 + rng.integers(0, 15))
            values = (
                ("process_cpu_percent", 92 + rng.normal(0, 1) if anomaly else 25 + rng.normal(0, 3), "%", "Gauge"),
                ("process_memory_bytes", (720 if anomaly else 420) * 1024**2 + rng.normal(0, 8 * 1024**2), "bytes", "Gauge"),
                ("http_request_duration_ms", 850 + rng.normal(0, 30) if anomaly else 55 + rng.normal(0, 6), "ms", "Gauge"),
                ("http_requests_total", counters[service], "requests", "Counter"),
            )
            for name, value, unit, metric_type in values:
                rows.append(
                    {
                        "timestamp": timestamp,
                        "metric_name": name,
                        "value": float(max(value, 0)),
                        "labels": json.dumps({"environment": "mock", "instance": PODS[service]}, sort_keys=True),
                        "service": service,
                        "pod": PODS[service],
                        "node": NODES[service],
                        "unit": unit,
                        "metric_type": metric_type,
                        "source": "synthetic-prometheus-mock",
                    }
                )
    return pd.DataFrame(rows)


def build_traces(start: datetime) -> pd.DataFrame:
    rows = []
    chain = ("frontend", "checkout", "payment", "database")
    operations = ("GET /checkout", "CreateOrder", "AuthorizePayment", "SELECT inventory")
    for minute in range(60):
        current_trace = trace_id(minute)
        anomaly = 30 <= minute <= 38
        span_ids = [f"{(minute + 1) * 16 + index:016x}" for index in range(4)]
        durations = [1100, 980, 320, 760] if anomaly else [95, 70, 35, 20]
        for index, service in enumerate(chain):
            rows.append(
                {
                    "trace_id": current_trace,
                    "span_id": span_ids[index],
                    "parent_span_id": "" if index == 0 else span_ids[index - 1],
                    "parent_service": "" if index == 0 else chain[index - 1],
                    "service": service,
                    "operation_name": operations[index],
                    "start_time": start + timedelta(minutes=minute, milliseconds=index * 4),
                    "duration_ms": float(durations[index]),
                    "status": "ERROR" if anomaly and service in {"checkout", "database"} else "OK",
                    "attributes": json.dumps({"mock": True, "http.method": "GET"}, sort_keys=True),
                    "resource_attributes": json.dumps(
                        {"service.name": service, "k8s.pod.name": PODS[service], "k8s.node.name": NODES[service]},
                        sort_keys=True,
                    ),
                }
            )
    return pd.DataFrame(rows)


def build_logs(start: datetime) -> pd.DataFrame:
    rows = []
    for minute in range(60):
        current_trace = trace_id(minute)
        for index, service in enumerate(SERVICES):
            anomaly = 30 <= minute <= 38 and service == "checkout"
            rows.append(
                {
                    "timestamp": start + timedelta(minutes=minute, seconds=index),
                    "service": service,
                    "pod": PODS[service],
                    "severity": "ERROR" if anomaly else "INFO",
                    "body": "mock database timeout while processing checkout" if anomaly else "mock request completed",
                    "attributes": json.dumps({"mock": True, "environment": "MOCK_ONLY"}, sort_keys=True),
                    "trace_id": current_trace,
                    "span_id": f"{(minute + 1) * 16 + index:016x}",
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock-root", type=Path, default=Path("/data/aiops-contest/mock-only"))
    parser.add_argument("--staging-root", type=Path, default=Path("/data/aiops-contest/staging"))
    args = parser.parse_args()
    target = args.mock_root / "cases" / CASE_ID / VERSION
    manifest_path = args.mock_root / "manifests" / f"{CASE_ID}.json"
    if target.exists() and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result = validate_case_directory(target, manifest)
        if result["status"] != "PASS":
            raise SystemExit(json.dumps(result, indent=2))
        print(json.dumps({"status": "EXISTS_VALID", "case_id": CASE_ID}))
        return

    args.staging_root.mkdir(parents=True, exist_ok=True)
    stage = args.staging_root / f"mock-{CASE_ID}-{uuid.uuid4().hex}"
    stage.mkdir(mode=0o700)
    try:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        metrics = build_metrics(start)
        logs = build_logs(start)
        traces = build_traces(start).sort_values(["trace_id", "start_time", "span_id"])
        metrics.to_parquet(stage / "metrics.parquet", index=False, compression="zstd")
        logs.to_parquet(stage / "logs.parquet", index=False, compression="zstd")
        traces.to_parquet(stage / "traces.parquet", index=False, compression="zstd", row_group_size=4)
        trace_index = (
            traces.groupby("trace_id", as_index=False)
            .agg(start_time=("start_time", "min"), span_count=("span_id", "count"))
            .sort_values("trace_id")
        )
        trace_index.to_parquet(stage / "trace_index.parquet", index=False, compression="zstd")
        files = []
        row_counts = {
            "metrics.parquet": len(metrics), "logs.parquet": len(logs),
            "traces.parquet": len(traces), "trace_index.parquet": len(trace_index),
        }
        for name in ("metrics.parquet", "logs.parquet", "traces.parquet", "trace_index.parquet"):
            path = stage / name
            files.append(
                {"name": name, "sha256": sha256_file(path), "size_bytes": path.stat().st_size,
                 "row_count": row_counts[name]}
            )
        topology = {
            "nodes": [
                {"service": service, "pod": PODS[service], "node": NODES[service]}
                for service in SERVICES
            ],
            "edges": [
                {"from": "frontend", "to": "checkout"},
                {"from": "checkout", "to": "payment"},
                {"from": "payment", "to": "database"},
            ],
        }
        manifest = {
            "case_id": CASE_ID,
            "version": VERSION,
            "dataset_schema_version": "1.0.0",
            "start_time": start.isoformat().replace("+00:00", "Z"),
            "end_time": (start + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "available_modalities": ["metrics", "logs", "traces"],
            "public_services": list(SERVICES),
            "public_topology": topology,
            "data_source": "SYNTHETIC_MOCK_ONLY",
            "classification": "MOCK_ONLY",
            "publication_status": "mock_published",
            "files": files,
            "sample_trace_id": trace_id(0),
        }
        manifest["immutable_manifest_hash"] = manifest_hash(manifest)
        result = validate_case_directory(stage, manifest)
        if result["status"] != "PASS":
            raise RuntimeError(json.dumps(result, indent=2))
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise RuntimeError(f"target exists without published manifest: {target}")
        os.rename(stage, target)
        args.mock_root.joinpath("manifests").mkdir(parents=True, exist_ok=True)
        temporary_manifest = manifest_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary_manifest, manifest_path)
        print(json.dumps({"status": "CREATED", "case_id": CASE_ID, "validation": result}, indent=2))
    except Exception:
        if stage.exists():
            error_path = stage / "generation-error.txt"
            error_path.write_text("Mock generation failed; staging preserved for inspection.\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
