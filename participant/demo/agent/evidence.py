from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from client.public_api import PublicAPI


def evidence_from_record(modality: str, record: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in record.items() if not k.startswith("_")}
    summary_fields = {
        key: clean.get(key)
        for key in (
            "timestamp", "start_time", "metric_name", "value", "service", "pod",
            "severity", "body", "trace_id", "span_id", "parent_span_id",
            "parent_service", "duration_ms", "status",
        )
        if clean.get(key) is not None
    }
    return {
        "modality": modality,
        "request_id": record.get("_request_id"),
        "record_sha256": PublicAPI.record_digest(record),
        "record": summary_fields,
    }


def validate_evidence(anomalies: list[dict[str, Any]], run_dir: str | Path) -> dict[str, Any]:
    seen_request_ids: set[str] = set()
    log_path = Path(run_dir) / "api_requests.jsonl"
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            try:
                seen_request_ids.add(json.loads(line)["request_id"])
            except Exception:
                continue
    checks: list[dict[str, Any]] = []
    valid = True
    for anomaly_index, anomaly in enumerate(anomalies):
        evidence = anomaly.get("evidence") or []
        if not evidence:
            valid = False
            checks.append({"anomaly": anomaly_index, "valid": False, "reason": "missing evidence"})
            continue
        for item in evidence:
            record = item.get("record") or {}
            digest = hashlib.sha256(
                json.dumps(record, ensure_ascii=False, sort_keys=True, default=str).encode()
            ).hexdigest()
            request_ok = item.get("request_id") in seen_request_ids or item.get("source") == "mcp"
            item_ok = bool(record) and bool(item.get("record_sha256")) and request_ok
            checks.append(
                {
                    "anomaly": anomaly_index,
                    "request_id": item.get("request_id"),
                    "valid": item_ok,
                    "summary_digest": digest,
                    "reason": None if item_ok else "record/request reference unavailable",
                }
            )
            valid = valid and item_ok
    result = {"valid": valid, "checks": checks, "anomaly_count": len(anomalies)}
    (Path(run_dir) / "evidence_validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result

