#!/usr/bin/env python3
"""Classify Collector refusal intervals without equating refusal with lost spans."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def around(points, start, end, pad=10):
    return [item for item in points if start - pad <= item[0] <= end + pad]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-analysis", type=Path, required=True)
    parser.add_argument("--previous-gap", type=Path, required=True)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--resources", type=Path, required=True)
    args = parser.parse_args()
    if args.timeline.exists() or args.resources.exists():
        raise ValueError("Refusing overwrite")
    source = json.loads(args.raw_analysis.read_text())
    gap = json.loads(args.previous_gap.read_text())
    history = source["windows"]["historical_gap"]["metrics"]
    recent = source["windows"]["recent_2h"]["metrics"]
    events = []
    for key, row in (("historical_gap", history), ("recent_2h", recent)):
        for interval in row["limiter_refused_spans"]["summary"]["positive_intervals"]:
            start, end = interval["from_scrape"], interval["to_scrape"]
            peer = {}
            for metric in ("receiver_refused_grpc", "limiter_refused_logs",
                           "heap_bytes", "working_set_bytes", "rss_bytes",
                           "jaeger_queue_size", "jaeger_sent_spans"):
                points = around(row[metric]["actual_scrapes"], start, end)
                peer[metric] = {"samples": len(points), "values": points,
                    "min": min((item[1] for item in points), default=None),
                    "max": max((item[1] for item in points), default=None)}
            events.append({"window": key, "from_scrape_utc": iso(start),
                "to_scrape_utc": iso(end),
                "observed_limiter_refused_spans": interval["observed_counter_increase"],
                "context": peer,
                "per_trace_final_delivery": "UNATTRIBUTED_BY_AGGREGATE_COUNTER"})
    counted = ("receiver_refused_grpc", "limiter_refused_spans", "limiter_refused_logs",
               "limiter_refused_metrics", "receiver_failed_grpc", "jaeger_sent_spans")
    history_deltas = {metric: history[metric]["summary"]["observed_increase"]
                      for metric in counted}
    recent_deltas = {metric: recent[metric]["summary"]["observed_increase"]
                     for metric in counted}
    candidate = {
        "component": "otel-collector gateway, not otel-cluster-collector",
        "configuration_issue": "A 25% spike reserve leaves a 220 MiB soft limit under a 400 MiB container limit; observed heap crosses soft limit while working set stays below container limit",
        "single_minimal_candidate": "memory_limiter.spike_limit_percentage: 25 -> 15",
        "unchanged": ["container memory limit 400Mi", "GOMEMLIMIT 320MiB",
            "memory_limiter hard limit 80%=320MiB", "check_interval 5s",
            "batch/exporter/queue/retry configuration", "service/Proxy sampling and workload"],
        "candidate_soft_limit_mib": 260,
        "observed_heap_peak_mib": source["gateway_memory_contract"]["heap_peak_mib_recent_2h"],
        "observed_working_set_peak_mib": source["gateway_memory_contract"]["working_set_peak_mib_recent_2h"],
        "risk": "Future heap and cgroup spikes can exceed recent observations; independent rollback and hard stop at container pressure or restart required",
        "not_applied_by_analysis": True}
    timeline = {"status": "REFUSAL_TIMELINE_PARTIALLY_LOCALIZED",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "gateway_pod": source["gateway_pod"],
        "historical_window": source["windows"]["historical_gap"]["start_utc"] +
            " / " + source["windows"]["historical_gap"]["end_utc"],
        "historical_proxy_missing_completion_range": gap["missing_completion_window_utc"],
        "historical_observed_counter_deltas": history_deltas,
        "recent_observed_counter_deltas": recent_deltas,
        "events": events,
        "gateway_raw_scrape_source_sha256": sha(args.raw_analysis),
        "previous_gap_source_sha256": sha(args.previous_gap),
        "historical_32_proxy_spans_missing_in_later_storage": True,
        "historical_32_directly_attributed_to_limiter": False,
        "retry_success_for_any_specific_refused_span": "UNVERIFIED",
        "counter_reset_intervals": {key: {metric: row[metric]["summary"]["reset_intervals"]
            for metric in ("receiver_refused_grpc", "limiter_refused_spans")}
            for key, row in (("historical_gap", history), ("recent_2h", recent))},
        "private_gt_read": False}
    resources = {"status": "MEMORY_LIMITER_SOFT_THRESHOLD_MISMATCH_SUPPORTED",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "gateway_memory_contract": source["gateway_memory_contract"],
        "gateway_resource_metrics": {metric: recent[metric]["summary"] for metric in
            ("heap_bytes", "rss_bytes", "working_set_bytes", "jaeger_queue_size",
             "jaeger_queue_capacity")},
        "raw_scrape_identity": {metric: recent[metric]["metric"] for metric in
            ("receiver_refused_grpc", "limiter_refused_spans", "heap_bytes")},
        "candidate": candidate,
        "exporter_failed_spans_metric_status": "MISSING_SERIES_UNKNOWN_NOT_ZERO",
        "export_retry_metric_status": "MISSING_SERIES_UNKNOWN_NOT_ZERO",
        "storage_backpressure_proven": False,
        "raw_analysis_sha256": sha(args.raw_analysis),
        "private_gt_read": False}
    args.timeline.parent.mkdir(parents=True, exist_ok=True)
    args.resources.parent.mkdir(parents=True, exist_ok=True)
    args.timeline.write_text(json.dumps(timeline, indent=2) + "\n")
    args.resources.write_text(json.dumps(resources, indent=2) + "\n")
    print(json.dumps({"status": resources["status"], "history": history_deltas,
                      "recent": recent_deltas, "event_count": len(events)}))


if __name__ == "__main__":
    main()
