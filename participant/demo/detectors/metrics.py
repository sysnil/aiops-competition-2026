from __future__ import annotations

from collections import defaultdict
from typing import Any

from .common import number, parse_time, robust_score


KNOWN_GAUGES = {
    "container_memory_working_set_bytes",
    "k8s_node_memory_available_bytes",
    "k8s_node_cpu_usage",
    "k8s_node_condition_ready",
}
KNOWN_COUNTERS = {
    "traces_span_metrics_calls_total",
    "container_cpu_usage_seconds_total",
    "container_cpu_cfs_periods_total",
    "container_cpu_cfs_throttled_periods_total",
    "container_cpu_cfs_throttled_seconds_total",
    "k8s_node_cpu_time_seconds_total",
}


def metric_kind(row: dict[str, Any]) -> str:
    explicit = str(row.get("metric_type") or "").lower()
    if explicit in {"gauge", "counter"}:
        return explicit
    name = str(row.get("metric_name") or "").lower()
    if name in KNOWN_GAUGES:
        return "gauge"
    if name in KNOWN_COUNTERS:
        return "counter"
    return ""


def series_key(row: dict[str, Any]) -> tuple[Any, ...]:
    labels = row.get("labels") or {}
    label_key = tuple(sorted(labels.items())) if isinstance(labels, dict) else str(labels)
    return (
        row.get("metric_name"), row.get("service"), row.get("pod"),
        row.get("node"), label_key,
    )


def detect_gauges(rows: list[dict[str, Any]], z_threshold: float = 5.0) -> dict[str, Any]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    counters_withheld = 0
    unknown_type_withheld = 0
    for row in rows:
        metric_type = metric_kind(row)
        if metric_type == "counter":
            counters_withheld += 1
            continue
        if metric_type != "gauge":
            unknown_type_withheld += 1
            continue
        if number(row.get("value")) is not None:
            groups[series_key(row)].append(row)
    candidates: list[dict[str, Any]] = []
    for key, samples in groups.items():
        if len(samples) < 5:
            continue
        values = [number(x.get("value")) for x in samples]
        valid = [x for x in values if x is not None]
        for row in samples:
            value = number(row.get("value"))
            if value is None:
                continue
            score, median, mad = robust_score(value, valid)
            if abs(score) >= z_threshold:
                if row.get("service"):
                    entity_type, entity_id = "Service", row["service"]
                elif row.get("pod"):
                    entity_type, entity_id = "Pod", row["pod"]
                elif row.get("node"):
                    entity_type, entity_id = "Node", row["node"]
                else:
                    entity_type, entity_id = "MetricSeries", row.get("metric_name") or "unknown"
                candidates.append(
                    {
                        "modality": "metrics",
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "metric_name": row.get("metric_name"),
                        "anomaly_type": "gauge_outlier",
                        "start_time": row.get("timestamp"),
                        "end_time": row.get("timestamp"),
                        "score": round(abs(score), 3),
                        "summary": f"{row.get('metric_name')}={value:g}, robust baseline median={median:g}, MAD={mad:g}",
                        "evidence_records": [row],
                    }
                )
    return {
        "candidates": sorted(candidates, key=lambda x: x["score"], reverse=True)[:20],
        "counter_samples_withheld": counters_withheld,
        "unknown_type_samples_withheld": unknown_type_withheld,
        "counter_misuse_count": 0,
    }


def detect_counter_rates(
    rows: list[dict[str, Any]],
    z_threshold: float = 5.0,
    ratio_threshold: float = 2.0,
    max_gap_seconds: float = 900.0,
) -> dict[str, Any]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if metric_kind(row) == "counter":
            groups[series_key(row)].append(row)
    derived: list[dict[str, Any]] = []
    reset_count = 0
    invalid_count = 0
    for key, samples in groups.items():
        samples.sort(key=lambda r: parse_time(r["timestamp"]))
        for previous, current in zip(samples, samples[1:]):
            dt = (parse_time(current["timestamp"]) - parse_time(previous["timestamp"])).total_seconds()
            before, after = number(previous.get("value")), number(current.get("value"))
            if before is None or after is None or dt <= 0 or dt > max_gap_seconds:
                invalid_count += 1
                continue
            delta = after - before
            if delta < 0:
                reset_count += 1
                continue
            derived.append({"key": key, "rate": delta / dt, "previous": previous, "current": current})
    by_metric: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in derived:
        by_metric[str(item["key"][0])].append(item)
    candidates: list[dict[str, Any]] = []
    for metric_name, samples in by_metric.items():
        all_rates = [x["rate"] for x in samples]
        if len(all_rates) < 5:
            continue
        for item in samples:
            score, median, mad = robust_score(item["rate"], all_rates)
            ratio = item["rate"] / max(abs(median), 1e-9)
            if score >= z_threshold and ratio >= ratio_threshold:
                current = item["current"]
                if current.get("service"):
                    entity_type, entity_id = "Service", current["service"]
                elif current.get("pod"):
                    entity_type, entity_id = "Pod", current["pod"]
                elif current.get("node"):
                    entity_type, entity_id = "Node", current["node"]
                else:
                    entity_type, entity_id = "MetricSeries", metric_name
                candidates.append(
                    {
                        "modality": "metrics",
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "anomaly_type": "counter_rate_spike",
                        "start_time": item["previous"].get("timestamp"),
                        "end_time": current.get("timestamp"),
                        "score": round(score, 3),
                        "summary": f"{metric_name} rate={item['rate']:.6g}/s, baseline median={median:.6g}/s, MAD={mad:.6g}",
                        "evidence_records": [item["previous"], current],
                        "counter_semantics": "rate_from_monotonic_delta",
                    }
                )
    return {
        "candidates": sorted(candidates, key=lambda x: x["score"], reverse=True)[:20],
        "counter_series": len(groups),
        "derived_rates": len(derived),
        "reset_count": reset_count,
        "invalid_interval_count": invalid_count,
        "counter_misuse_count": 0,
    }
