from __future__ import annotations

from collections import defaultdict
from typing import Any

from .common import number, parse_time, percentile, robust_score


def detect_trace_latency(rows: list[dict[str, Any]], window_seconds: int = 300) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        duration = number(row.get("duration_ms"))
        if duration is None or not row.get("start_time"):
            continue
        service = str(row.get("service") or "unknown")
        bucket = int(parse_time(row["start_time"]).timestamp()) // window_seconds
        buckets[(service, bucket)].append(row)
    stats: dict[str, list[tuple[int, float, list[dict[str, Any]]]]] = defaultdict(list)
    for (service, bucket), samples in buckets.items():
        values = [number(x.get("duration_ms")) for x in samples]
        p95 = percentile([x for x in values if x is not None], 0.95)
        if p95 is not None and len(samples) >= 3:
            stats[service].append((bucket, p95, samples))
    candidates: list[dict[str, Any]] = []
    for service, series in stats.items():
        p95s = [x[1] for x in series]
        if len(p95s) < 3:
            continue
        baseline_bucket, baseline_p95, _ = min(series, key=lambda x: x[1])
        for bucket, p95, samples in series:
            score, median, mad = robust_score(p95, p95s)
            ratio = p95 / max(median, 1e-9)
            if (score >= 4 and ratio >= 1.5) or ratio >= 3:
                exemplars = sorted(samples, key=lambda x: number(x.get("duration_ms")) or 0, reverse=True)[:3]
                # The MCP edge must correspond to the representative anomalous span,
                # not merely the most frequent parent in the whole sampled window.
                parent = exemplars[0].get("parent_service") if exemplars else None
                candidates.append(
                    {
                        "modality": "traces",
                        "entity_type": "Service",
                        "entity_id": service,
                        "anomaly_type": "trace_latency_shift",
                        "start_time": min(x["start_time"] for x in samples),
                        "end_time": max(x["start_time"] for x in samples),
                        "score": round(max(score, ratio), 3),
                        "summary": f"trace p95={p95:.3f}ms versus median={median:.3f}ms, MAD={mad:.3f}, ratio={ratio:.2f}",
                        "evidence_records": exemplars,
                        "source_service": parent,
                        "target_service": service,
                        "baseline_bucket": baseline_bucket,
                        "analysis_bucket": bucket,
                    }
                )
    # A targeted query may contain fewer than three complete baseline buckets.
    # Preserve a conservative, generic fallback for exceptionally long spans
    # that also carry an explicit ERROR status. Duration alone is insufficient:
    # ordinary successful traces must never become anomalies merely for existing.
    existing = {
        (str(item.get("entity_id")), int(parse_time(item["start_time"]).timestamp()) // window_seconds)
        for item in candidates
    }
    extreme: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        duration = number(row.get("duration_ms"))
        if (
            duration is not None
            and duration >= 10_000
            and str(row.get("status") or "").upper() == "ERROR"
            and row.get("start_time")
        ):
            service = str(row.get("service") or "unknown")
            bucket = int(parse_time(row["start_time"]).timestamp()) // window_seconds
            extreme[(service, bucket)].append(row)
    for (service, bucket), samples in extreme.items():
        if (service, bucket) in existing:
            continue
        exemplars = sorted(samples, key=lambda x: number(x.get("duration_ms")) or 0, reverse=True)[:3]
        durations = [number(item.get("duration_ms")) for item in samples]
        p95 = percentile([item for item in durations if item is not None], 0.95) or 0
        candidates.append(
            {
                "modality": "traces",
                "entity_type": "Service",
                "entity_id": service,
                "anomaly_type": "extreme_error_trace_latency",
                "start_time": min(item["start_time"] for item in samples),
                "end_time": max(item["start_time"] for item in samples),
                "score": round(max(10.0, p95 / 1000.0), 3),
                "summary": (
                    f"{len(samples)} ERROR spans exceeded 10000ms in {window_seconds}s; "
                    f"p95={p95:.3f}ms"
                ),
                "evidence_records": exemplars,
                "source_service": exemplars[0].get("parent_service") if exemplars else None,
                "target_service": service,
                "analysis_bucket": bucket,
                "baseline_bucket": None,
            }
        )
    return sorted(candidates, key=lambda x: x["score"], reverse=True)[:20]
