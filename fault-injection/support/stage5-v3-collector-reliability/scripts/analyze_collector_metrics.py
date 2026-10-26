#!/usr/bin/env python3
"""Recover actual scrape timestamps and analyze one Collector's counters."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import urllib.parse
import urllib.request


PROM = "http://10.97.14.138:9090"
POD = "otel-collector-7f5c5999c-nz27f"
EXPRESSIONS = {
    "receiver_accepted_grpc": 'otelcol_receiver_accepted_spans_total{k8s_pod_name="' + POD + '",transport="grpc"}',
    "receiver_refused_grpc": 'otelcol_receiver_refused_spans_total{k8s_pod_name="' + POD + '",transport="grpc"}',
    "receiver_failed_grpc": 'otelcol_receiver_failed_spans_total{k8s_pod_name="' + POD + '",transport="grpc"}',
    "limiter_accepted_spans": 'otelcol_processor_memory_limiter_accepted_spans_total{k8s_pod_name="' + POD + '"}',
    "limiter_refused_spans": 'otelcol_processor_memory_limiter_refused_spans_total{k8s_pod_name="' + POD + '"}',
    "limiter_refused_logs": 'otelcol_processor_memory_limiter_refused_log_records_total{k8s_pod_name="' + POD + '"}',
    "limiter_refused_metrics": 'otelcol_processor_memory_limiter_refused_metric_points_total{k8s_pod_name="' + POD + '"}',
    "jaeger_sent_spans": 'otelcol_exporter_sent_spans_total{k8s_pod_name="' + POD + '",exporter="otlp_grpc/jaeger"}',
    "jaeger_queue_size": 'otelcol_exporter_queue_size{k8s_pod_name="' + POD + '",exporter="otlp_grpc/jaeger"}',
    "jaeger_queue_capacity": 'otelcol_exporter_queue_capacity{k8s_pod_name="' + POD + '",exporter="otlp_grpc/jaeger"}',
    "heap_bytes": 'otelcol_process_runtime_heap_alloc_bytes{k8s_pod_name="' + POD + '"}',
    "rss_bytes": 'otelcol_process_memory_rss_bytes{k8s_pod_name="' + POD + '"}',
    "working_set_bytes": 'container_memory_working_set_bytes{namespace="otel-demo",pod="' + POD + '",container="opentelemetry-collector"}',
}
COUNTERS = {"receiver_accepted_grpc", "receiver_refused_grpc", "receiver_failed_grpc",
            "limiter_accepted_spans", "limiter_refused_spans", "limiter_refused_logs",
            "limiter_refused_metrics", "jaeger_sent_spans"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def query(expression, start, end, step):
    url = PROM + "/api/v1/query_range?" + urllib.parse.urlencode({
        "query": expression, "start": start, "end": end, "step": step})
    with urllib.request.urlopen(url, timeout=35) as response:
        value = json.load(response)
    if value.get("status") != "success":
        raise RuntimeError("Prometheus query failure: " + expression)
    return value


def actual_samples(name, expression, start, end, step, output):
    values = query(expression, start, end, step)
    stamps = query("timestamp(" + expression + ")", start, end, step)
    for suffix, value in (("values", values), ("scrape-timestamps", stamps)):
        path = output / (name + "-" + suffix + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n")
    sources = {suffix: sha(output / (name + "-" + suffix + ".json"))
               for suffix in ("values", "scrape-timestamps")}
    left, right = values["data"]["result"], stamps["data"]["result"]
    if (len(left) != 1 or len(right) != 1 or
            {key: value for key, value in left[0]["metric"].items() if key != "__name__"} !=
            {key: value for key, value in right[0]["metric"].items() if key != "__name__"}):
        raise ValueError("Metric missing or mixed Collector series: " + name)
    labels = left[0]["metric"]
    if name != "working_set_bytes" and labels.get("k8s_pod_name") != POD:
        raise ValueError("Unexpected Collector Pod label")
    if name == "working_set_bytes" and labels.get("pod") != POD:
        raise ValueError("Unexpected container Pod label")
    samples = {}
    for (eval_left, text_value), (eval_right, actual) in zip(left[0]["values"], right[0]["values"]):
        if eval_left != eval_right:
            raise ValueError("Evaluation grid mismatch")
        value = float(text_value)
        stamp = float(actual)
        if not math.isfinite(value) or not math.isfinite(stamp):
            continue
        if stamp in samples and samples[stamp] != value:
            raise ValueError("Conflicting value for one actual scrape")
        samples[stamp] = value
    ordered = [[stamp, value] for stamp, value in sorted(samples.items())]
    return {"metric": labels, "raw_sha256": sources, "actual_scrapes": ordered,
            "evaluation_points": len(left[0]["values"])}


def counter_summary(points):
    transitions, resets, gaps = [], [], []
    for before, after in zip(points, points[1:]):
        delta = after[1] - before[1]
        interval = after[0] - before[0]
        if interval > 30:
            gaps.append({"from": before[0], "to": after[0], "seconds": interval})
        if delta < 0:
            resets.append({"from": before[0], "to": after[0], "delta": delta})
        elif delta > 0:
            transitions.append({"from_scrape": before[0], "to_scrape": after[0],
                                "observed_counter_increase": delta})
    return {"first": points[0] if points else None, "last": points[-1] if points else None,
            "actual_scrape_count": len(points), "positive_intervals": transitions,
            "reset_intervals": resets, "gaps_over_30_seconds": gaps,
            "observed_increase": sum(row["observed_counter_increase"] for row in transitions),
            "first_last_difference_if_no_reset": points[-1][1] - points[0][1]
                if points and not resets else None}


def gauge_summary(points):
    values = sorted(row[1] for row in points)
    return {"actual_scrape_count": len(points), "min": values[0] if values else None,
            "p50": values[int((len(values) - 1) * .5)] if values else None,
            "p95": values[int((len(values) - 1) * .95)] if values else None,
            "max": values[-1] if values else None,
            "last": points[-1] if points else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    if args.output_root.exists():
        raise ValueError("Refusing overwrite")
    args.output_root.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    windows = {
        "historical_gap": (datetime(2026, 10, 12, 10, 44, 55, tzinfo=timezone.utc),
                           datetime(2026, 10, 12, 10, 46, 10, tzinfo=timezone.utc), 5),
        "recent_2h": (now - timedelta(hours=2), now - timedelta(seconds=15), 5),
    }
    result = {"status": "ONE_GATEWAY_RAW_SCRAPE_SERIES_AUDITED",
        "captured_at_utc": now.isoformat(), "gateway_pod": POD,
        "counter_values_are_raw_scrape_values_not_rate_extrapolation": True,
        "windows": {}, "missing_series_are_unknown_not_zero": True}
    for name, (start, end, step) in windows.items():
        summary = {"start_utc": start.isoformat(), "end_utc": end.isoformat(),
                   "evaluation_step_seconds": step, "metrics": {}}
        for metric, expression in EXPRESSIONS.items():
            source = actual_samples(metric, expression, start.timestamp(), end.timestamp(),
                                    step, args.output_root / "raw" / name)
            points = source.pop("actual_scrapes")
            details = counter_summary(points) if metric in COUNTERS else gauge_summary(points)
            summary["metrics"][metric] = {"promql": expression,
                **source, "actual_scrapes": points, "summary": details}
        result["windows"][name] = summary
    historical = result["windows"]["historical_gap"]["metrics"]
    recent = result["windows"]["recent_2h"]["metrics"]
    result["gateway_memory_contract"] = {"container_limit_mib": 400,
        "gomemlimit_mib": 320, "memory_limiter_hard_mib": 320,
        "memory_limiter_soft_mib": 220,
        "working_set_peak_mib_recent_2h": recent["working_set_bytes"]["summary"]["max"] / 1048576,
        "heap_peak_mib_recent_2h": recent["heap_bytes"]["summary"]["max"] / 1048576}
    result["historical_refusal_intervals"] = {key: historical[key]["summary"]["positive_intervals"]
        for key in ("receiver_refused_grpc", "limiter_refused_spans", "limiter_refused_logs")}
    result["recent_refusal_intervals"] = {key: recent[key]["summary"]["positive_intervals"]
        for key in ("receiver_refused_grpc", "limiter_refused_spans", "limiter_refused_logs")}
    (args.output_root / "analysis.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "memory": result["gateway_memory_contract"],
        "recent_limiter_refused": recent["limiter_refused_spans"]["summary"]["observed_increase"],
        "historical_limiter_refused": historical["limiter_refused_spans"]["summary"]["observed_increase"]}))


if __name__ == "__main__":
    main()
