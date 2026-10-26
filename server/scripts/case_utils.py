from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq


CASE_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,63}$")
FORBIDDEN_MARKERS = (
    "ground_truth",
    "ground-truth",
    "fault_injection",
    "injection_schedule",
    "private_answer",
    "scoring_answer",
    "root_cause_answer",
)
REQUIRED_COLUMNS = {
    "metrics.parquet": {
        "timestamp",
        "metric_name",
        "value",
        "labels",
        "service",
        "pod",
        "node",
        "unit",
        "metric_type",
        "source",
    },
    "logs.parquet": {
        "timestamp",
        "service",
        "pod",
        "severity",
        "body",
        "attributes",
        "trace_id",
        "span_id",
    },
    "traces.parquet": {
        "trace_id",
        "span_id",
        "parent_span_id",
        "parent_service",
        "service",
        "operation_name",
        "start_time",
        "duration_ms",
        "status",
        "attributes",
        "resource_attributes",
    },
}


def expected_duration_seconds(case_id: str) -> int:
    match = re.fullmatch(r"SF-(\d{3})", case_id)
    return 1800 if match and 3 <= int(match.group(1)) <= 17 else 3600


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include timezone")
    return parsed.astimezone(timezone.utc)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_hash(manifest: dict[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items() if key != "immutable_manifest_hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _scan_forbidden(value: Any, path: str = "manifest") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in FORBIDDEN_MARKERS):
                errors.append(f"forbidden private metadata key at {path}.{key}")
            errors.extend(_scan_forbidden(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_scan_forbidden(child, f"{path}[{index}]"))
    return errors


def validate_case_directory(case_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    stats: dict[str, Any] = {}
    case_id = str(manifest.get("case_id", ""))
    if not CASE_ID_RE.fullmatch(case_id):
        errors.append("invalid case_id")
    try:
        start = utc(manifest["start_time"])
        end = utc(manifest["end_time"])
        expected_seconds = expected_duration_seconds(case_id)
        if start >= end or (end - start).total_seconds() != expected_seconds:
            errors.append(f"case window must be exactly {expected_seconds} seconds")
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"invalid case time window: {exc}")
        start = end = datetime.now(timezone.utc)
    errors.extend(_scan_forbidden(manifest))
    allowed_source_conditions = manifest.get("allowed_source_conditions", {})
    observed_source_conditions: dict[str, int] = {}

    def check_source_condition(name: str, actual: int) -> None:
        observed_source_conditions[name] = actual
        try:
            expected = int(allowed_source_conditions.get(name, 0))
        except (TypeError, ValueError):
            errors.append(f"invalid allowed source condition: {name}")
            return
        if actual != expected:
            errors.append(f"source condition mismatch for {name}: expected {expected}, got {actual}")

    file_records = {item.get("name"): item for item in manifest.get("files", [])}
    for filename, record in file_records.items():
        if not isinstance(filename, str) or Path(filename).name != filename:
            errors.append(f"unsafe manifest filename: {filename}")
            continue
        path = case_dir / filename
        if not path.is_file():
            errors.append(f"missing manifest file: {filename}")
        elif record.get("sha256") != sha256_file(path):
            errors.append(f"SHA-256 mismatch: {filename}")
    frames: dict[str, pd.DataFrame] = {}
    for filename, required in REQUIRED_COLUMNS.items():
        path = case_dir / filename
        if not path.is_file():
            errors.append(f"missing {filename}")
            continue
        if any(marker in filename.lower() for marker in FORBIDDEN_MARKERS):
            errors.append(f"forbidden filename: {filename}")
        record = file_records.get(filename)
        if not record:
            errors.append(f"manifest record missing: {filename}")
        try:
            schema_names = set(pq.read_schema(path).names)
            missing = required - schema_names
            if missing:
                errors.append(f"{filename} missing columns: {sorted(missing)}")
            if any(any(marker in column.lower() for marker in FORBIDDEN_MARKERS) for column in schema_names):
                errors.append(f"{filename} contains forbidden private column")
            frame = pd.read_parquet(path)
            frames[filename] = frame
            stats[filename] = {"rows": len(frame), "sha256": sha256_file(path)}
            if record and record.get("row_count") is not None and int(record["row_count"]) != len(frame):
                errors.append(f"row count mismatch: {filename}")
        except Exception as exc:  # validation must report corrupt packages
            errors.append(f"cannot read {filename}: {exc}")

    metrics = frames.get("metrics.parquet")
    if metrics is not None:
        metric_identity = ["timestamp", "metric_name", "service", "pod", "node", "labels"]
        duplicate_metric_identities = int(
            (metrics.groupby(metric_identity, dropna=False).size() > 1).sum()
        )
        duplicate_metric_full_rows = int((metrics.groupby(list(metrics.columns), dropna=False).size() > 1).sum())
        check_source_condition("duplicate_metric_identity_groups", duplicate_metric_identities)
        check_source_condition("duplicate_metric_full_row_groups", duplicate_metric_full_rows)
        metric_types = {str(value).lower() for value in metrics["metric_type"].dropna().unique()}
        if not metric_types.issubset({"counter", "gauge", "histogram"}):
            errors.append("unsupported metric_type")
        timestamps = pd.to_datetime(metrics["timestamp"], utc=True)
        if ((timestamps < start) | (timestamps >= end)).any():
            errors.append("metrics timestamp outside case window")

    logs = frames.get("logs.parquet")
    if logs is not None:
        timestamps = pd.to_datetime(logs["timestamp"], utc=True)
        if ((timestamps < start) | (timestamps >= end)).any():
            errors.append("logs timestamp outside case window")

    traces = frames.get("traces.parquet")
    if traces is not None:
        timestamps = pd.to_datetime(traces["start_time"], utc=True)
        if ((timestamps < start) | (timestamps >= end)).any():
            errors.append("trace timestamp outside case window")
        if traces.duplicated(["trace_id", "span_id"]).any():
            errors.append("duplicate trace/span identity")
        span_sets = traces.groupby("trace_id")["span_id"].apply(set).to_dict()
        unresolved_parent_spans = 0
        for row in traces.itertuples(index=False):
            if pd.notna(row.parent_span_id) and row.parent_span_id != "" and row.parent_span_id not in span_sets.get(row.trace_id, set()):
                unresolved_parent_spans += 1
        check_source_condition("unresolved_parent_spans", unresolved_parent_spans)
        if logs is not None:
            trace_ids = set(traces["trace_id"].dropna())
            linked_logs = logs[logs["trace_id"].notna() & (logs["trace_id"] != "")]
            unresolved_log_trace_rows = int((~linked_logs["trace_id"].isin(trace_ids)).sum())
            check_source_condition("unresolved_log_trace_rows", unresolved_log_trace_rows)

    expected_hash = manifest.get("immutable_manifest_hash")
    if expected_hash and expected_hash != manifest_hash(manifest):
        errors.append("immutable manifest hash mismatch")
    stats["observed_source_conditions"] = observed_source_conditions
    return {"status": "PASS" if not errors else "FAIL", "errors": errors, "stats": stats}
