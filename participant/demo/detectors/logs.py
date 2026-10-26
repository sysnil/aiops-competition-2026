from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .common import parse_time, robust_score


PATTERN = re.compile(r"\b(error|exception|failed|failure|timeout|reset|unavailable|refused|panic)\b", re.I)


def detect_log_bursts(rows: list[dict[str, Any]], window_seconds: int = 300) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    entity_types: dict[str, str] = {}
    for row in rows:
        timestamp = row.get("timestamp")
        if not timestamp:
            continue
        severity = str(row.get("severity") or "").upper()
        body = str(row.get("body") or "")
        if severity in {"ERROR", "FATAL", "CRITICAL"} or PATTERN.search(body):
            epoch = int(parse_time(timestamp).timestamp())
            entity = str(row.get("service") or row.get("pod") or "unknown")
            entity_types[entity] = "Service" if row.get("service") else ("Pod" if row.get("pod") else "Unknown")
            buckets[(entity, epoch // window_seconds)].append(row)
    by_service: dict[str, list[tuple[int, list[dict[str, Any]]]]] = defaultdict(list)
    for (entity, bucket), records in buckets.items():
        by_service[entity].append((bucket, records))
    candidates: list[dict[str, Any]] = []
    for service, series in by_service.items():
        counts = [len(records) for _, records in series]
        for bucket, records in series:
            score, median, mad = robust_score(float(len(records)), [float(x) for x in counts])
            # Targeted error queries may cover only one or two buckets. Five
            # explicit error-pattern records in such a bounded window are a
            # conservative candidate, not an automatic root-cause verdict.
            targeted_burst = len(series) < 3 and len(records) >= 5
            if targeted_burst or len(records) >= max(2, median + 2) or score >= 4:
                ordered = sorted(records, key=lambda x: x["timestamp"])
                candidates.append(
                    {
                        "modality": "logs",
                        "entity_type": entity_types.get(service, "Unknown"),
                        "entity_id": service,
                        "anomaly_type": "error_log_burst",
                        "start_time": ordered[0]["timestamp"],
                        "end_time": ordered[-1]["timestamp"],
                        "score": round(max(score, len(records) / max(median, 1)), 3),
                        "summary": f"{len(records)} suspicious logs in {window_seconds}s; service median={median:g}, MAD={mad:g}",
                        "evidence_records": ordered[:5],
                    }
                )
    return sorted(candidates, key=lambda x: x["score"], reverse=True)[:20]
