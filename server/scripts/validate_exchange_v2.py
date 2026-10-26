#!/usr/bin/env python3
"""Strictly validate an immutable CONTEST_EXCHANGE_V2 public package."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq


FILES = {
    "metrics.parquet",
    "logs.parquet",
    "traces.parquet",
    "trace_index.parquet",
    "topology.json",
    "manifest.json",
}
REQUIRED = {
    "metrics.parquet": {
        "timestamp", "metric_name", "value", "labels", "service", "pod",
        "node", "unit", "metric_type", "source",
    },
    "logs.parquet": {
        "timestamp", "service", "pod", "severity", "body", "attributes",
        "trace_id", "span_id",
    },
    "traces.parquet": {
        "trace_id", "span_id", "parent_span_id", "parent_service", "service",
        "operation_name", "start_time", "duration_ms", "status", "attributes",
        "resource_attributes",
    },
    "trace_index.parquet": {"trace_id", "start_time", "span_count"},
}
FORBIDDEN = (
    "ground_truth", "ground-truth", "private_gt", "fault_injection",
    "injection_schedule", "private_answer", "scoring_answer",
    "root_cause_answer", "fault_command",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def scalar(connection: duckdb.DuckDBPyConnection, sql: str, params: list[Any]) -> Any:
    return connection.execute(sql, params).fetchone()[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--expected-case-id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    package = args.package.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    result: dict[str, Any] = {"package": str(package)}

    actual_files = {path.name for path in package.iterdir() if path.is_file()}
    result["file_whitelist"] = {
        "expected": sorted(FILES), "actual": sorted(actual_files),
        "pass": actual_files == FILES,
    }
    if actual_files != FILES:
        errors.append(f"file whitelist mismatch: {sorted(actual_files ^ FILES)}")

    manifest_path = package / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    result["manifest_sha256"] = manifest_sha
    result["manifest"] = manifest
    if manifest.get("schema_version") != "CONTEST_EXCHANGE_V2":
        errors.append("unexpected schema_version")
    case_id = manifest.get("case_id")
    if ((args.expected_case_id is not None and case_id != args.expected_case_id) or
            not re.fullmatch(r"SF-\d{3}", str(case_id)) or
            not 1 <= int(str(case_id)[3:]) <= 17 or
            manifest.get("window", {}).get("case_id") != case_id):
        errors.append("unexpected case_id")
    start = utc(manifest["window"]["start_utc"])
    end = utc(manifest["window"]["end_utc"])
    expected_duration = 1800 if case_id in {
        f"SF-{index:03d}" for index in range(3, 18)} else 3600
    if (end - start).total_seconds() != expected_duration:
        errors.append(f"case window is not exactly {expected_duration} seconds")

    file_checks: dict[str, Any] = {}
    for name in sorted(FILES - {"manifest.json"}):
        path = package / name
        record = manifest.get("files", {}).get(name)
        if not path.is_file() or not isinstance(record, dict):
            errors.append(f"missing file or manifest record: {name}")
            continue
        actual_sha = sha256_file(path)
        actual_size = path.stat().st_size
        passed = actual_sha == record.get("sha256") and actual_size == record.get("bytes")
        file_checks[name] = {
            "sha256": actual_sha, "bytes": actual_size,
            "manifest_sha256": record.get("sha256"),
            "manifest_bytes": record.get("bytes"), "pass": passed,
        }
        if not passed:
            errors.append(f"hash or size mismatch: {name}")
    result["file_checks"] = file_checks

    parquet_schemas: dict[str, str] = {}
    for name, required in REQUIRED.items():
        schema = pq.read_schema(package / name)
        parquet_schemas[name] = str(schema)
        missing = required - set(schema.names)
        if missing:
            errors.append(f"{name} missing columns: {sorted(missing)}")
        if name in {"metrics.parquet", "logs.parquet"}:
            field = schema.field("timestamp")
        else:
            field = schema.field("start_time")
        if str(field.type) != "timestamp[ns, tz=UTC]":
            errors.append(f"{name} timestamp type is {field.type}, expected timestamp[ns, tz=UTC]")
        lowered = " ".join(schema.names).lower()
        if any(marker in lowered for marker in FORBIDDEN):
            errors.append(f"forbidden private column in {name}")
    result["parquet_schemas"] = parquet_schemas

    connection = duckdb.connect(":memory:")
    connection.execute("SET threads=2")
    connection.execute("SET memory_limit='2GB'")
    metrics = str(package / "metrics.parquet")
    logs = str(package / "logs.parquet")
    traces = str(package / "traces.parquet")
    index = str(package / "trace_index.parquet")

    counts = {
        "metrics": scalar(connection, "SELECT count(*) FROM read_parquet(?)", [metrics]),
        "logs": scalar(connection, "SELECT count(*) FROM read_parquet(?)", [logs]),
        "traces": scalar(connection, "SELECT count(*) FROM read_parquet(?)", [traces]),
        "trace_index": scalar(connection, "SELECT count(*) FROM read_parquet(?)", [index]),
        "unique_traces": scalar(connection, "SELECT count(DISTINCT trace_id) FROM read_parquet(?)", [traces]),
    }
    expected_counts = {
        "metrics": manifest.get("row_counts", {}).get("metrics"),
        "logs": manifest.get("row_counts", {}).get("logs"),
        "traces": manifest.get("row_counts", {}).get("traces"),
        "trace_index": manifest.get("trace_index_rows"),
        "unique_traces": manifest.get("unique_trace_count"),
    }
    result["row_counts"] = {"actual": counts, "expected": expected_counts}
    if counts != expected_counts:
        errors.append("row counts do not match manifest")

    ranges: dict[str, Any] = {}
    for name, path, column in (
        ("metrics", metrics, "timestamp"),
        ("logs", logs, "timestamp"),
        ("traces", traces, "start_time"),
    ):
        minimum, maximum, out_of_range = connection.execute(
            f"SELECT CAST(min({column}) AS VARCHAR), CAST(max({column}) AS VARCHAR), "
            f"count(*) FILTER (WHERE {column} < CAST(? AS TIMESTAMPTZ) OR {column} >= CAST(? AS TIMESTAMPTZ)) "
            "FROM read_parquet(?)", [start.isoformat(), end.isoformat(), path]
        ).fetchone()
        ranges[name] = {
            "min": minimum,
            "max": maximum,
            "outside_window": out_of_range,
        }
        if out_of_range:
            errors.append(f"{name} contains {out_of_range} rows outside the case window")
    result["time_ranges"] = ranges

    duplicate_metrics = scalar(
        connection,
        "SELECT count(*) FROM (SELECT 1 FROM read_parquet(?) GROUP BY timestamp, metric_name, service, pod, node, labels HAVING count(*) > 1)",
        [metrics],
    )
    duplicate_metric_full_rows = scalar(
        connection,
        "SELECT count(*) FROM (SELECT 1 FROM read_parquet(?) GROUP BY ALL HAVING count(*) > 1)",
        [metrics],
    )
    duplicate_spans = scalar(
        connection,
        "SELECT count(*) FROM (SELECT 1 FROM read_parquet(?) GROUP BY trace_id, span_id HAVING count(*) > 1)",
        [traces],
    )
    bad_trace_ids = scalar(
        connection,
        "SELECT count(*) FROM read_parquet(?) WHERE trace_id IS NULL OR NOT regexp_full_match(trace_id, '[0-9a-fA-F]{16,64}') OR span_id IS NULL OR NOT regexp_full_match(span_id, '[0-9a-fA-F]{16,64}')",
        [traces],
    )
    missing_parents = scalar(
        connection,
        "SELECT count(*) FROM read_parquet(?) c LEFT JOIN read_parquet(?) p ON c.trace_id=p.trace_id AND c.parent_span_id=p.span_id WHERE coalesce(c.parent_span_id, '') <> '' AND p.span_id IS NULL",
        [traces, traces],
    )
    missing_log_traces = scalar(
        connection,
        "SELECT count(*) FROM read_parquet(?) l LEFT JOIN (SELECT DISTINCT trace_id FROM read_parquet(?)) t USING(trace_id) WHERE coalesce(l.trace_id, '') <> '' AND t.trace_id IS NULL",
        [logs, traces],
    )
    index_mismatches = scalar(
        connection,
        "SELECT count(*) FROM read_parquet(?) i FULL JOIN (SELECT trace_id, min(start_time) start_time, count(*) span_count FROM read_parquet(?) GROUP BY trace_id) t USING(trace_id) WHERE i.trace_id IS NULL OR t.trace_id IS NULL OR i.start_time <> t.start_time OR i.span_count <> t.span_count",
        [index, traces],
    )
    relationships = {
        "duplicate_metric_identities": duplicate_metrics,
        "duplicate_metric_full_row_groups": duplicate_metric_full_rows,
        "duplicate_trace_span_identities": duplicate_spans,
        "invalid_trace_or_span_ids": bad_trace_ids,
        "unresolved_parent_spans": missing_parents,
        "log_trace_ids_missing_from_traces": missing_log_traces,
        "trace_index_mismatches": index_mismatches,
    }
    result["relationships"] = relationships
    for key in (
        "duplicate_metric_identities", "duplicate_trace_span_identities",
        "invalid_trace_or_span_ids", "trace_index_mismatches",
    ):
        if relationships[key]:
            errors.append(f"{key}: {relationships[key]}")
    if missing_parents:
        warnings.append(f"unresolved parent spans retained as supplied: {missing_parents}")
    if duplicate_metric_full_rows:
        warnings.append(f"exact duplicate metric row groups retained as supplied: {duplicate_metric_full_rows}")
    if missing_log_traces:
        warnings.append(f"log rows with trace IDs absent from public traces retained as supplied: {missing_log_traces}")

    nulls = {
        "metrics_pod": scalar(connection, "SELECT count(*) FROM read_parquet(?) WHERE pod IS NULL", [metrics]),
        "logs_severity": scalar(connection, "SELECT count(*) FROM read_parquet(?) WHERE severity IS NULL OR severity = ''", [logs]),
        "traces_status": scalar(connection, "SELECT count(*) FROM read_parquet(?) WHERE status IS NULL OR status = ''", [traces]),
        "traces_parent_span": scalar(connection, "SELECT count(*) FROM read_parquet(?) WHERE parent_span_id IS NULL OR parent_span_id = ''", [traces]),
    }
    result["optional_nulls"] = nulls

    invalid_json = {
        "metrics.labels": scalar(connection, "SELECT count(*) FROM read_parquet(?) WHERE labels IS NOT NULL AND NOT json_valid(labels)", [metrics]),
        "logs.attributes": scalar(connection, "SELECT count(*) FROM read_parquet(?) WHERE attributes IS NOT NULL AND NOT json_valid(attributes)", [logs]),
        "traces.attributes": scalar(connection, "SELECT count(*) FROM read_parquet(?) WHERE attributes IS NOT NULL AND NOT json_valid(attributes)", [traces]),
        "traces.resource_attributes": scalar(connection, "SELECT count(*) FROM read_parquet(?) WHERE resource_attributes IS NOT NULL AND NOT json_valid(resource_attributes)", [traces]),
    }
    result["invalid_json_fields"] = invalid_json
    if any(invalid_json.values()):
        errors.append(f"invalid canonical JSON fields: {invalid_json}")

    topology = json.loads((package / "topology.json").read_text(encoding="utf-8"))
    result["topology_type"] = type(topology).__name__
    result["topology_summary"] = {
        "top_level_keys": sorted(topology) if isinstance(topology, dict) else [],
        "bytes": (package / "topology.json").stat().st_size,
    }
    public_text = json.dumps({"manifest": manifest, "topology": topology}, ensure_ascii=False).lower()
    found_markers = sorted({marker for marker in FORBIDDEN if marker in public_text})
    if found_markers:
        errors.append(f"forbidden private markers in public JSON: {found_markers}")

    result["warnings"] = warnings
    result["errors"] = errors
    result["status"] = "PASS" if not errors else "FAIL"
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    raise SystemExit(0 if not errors else 1)


if __name__ == "__main__":
    main()
