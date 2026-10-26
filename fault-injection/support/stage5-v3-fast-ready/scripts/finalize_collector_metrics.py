#!/usr/bin/env python3
"""Distinguish supported current self-metrics from missing lazy counters."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path("/root/stage5-v3-fast-ready")
FULL = ROOT / "results/collector-metrics-full-20261013.json"
SMOKE = ROOT / "results/observability-smoke.json"
PRIOR = Path("/root/stage5-v3-collector-reliability/results/continuous-validation.json")
OUTPUT = ROOT / "results/collector-metrics.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUTPUT.exists():
        raise ValueError("Refusing overwrite")
    full, smoke, prior = [json.loads(path.read_text()) for path in (FULL, SMOKE, PRIOR)]
    metrics = full["metrics"]
    required = ("receiver_accepted_grpc", "receiver_refused_grpc", "limiter_accepted",
                "limiter_refused", "jaeger_sent", "jaeger_queue_size", "heap_bytes")
    current = all(metrics[name]["status"] == "OBSERVABLE" for name in required)
    refused = metrics["limiter_refused"]["observed_counter_delta"]
    if refused is None:
        classification = "UNKNOWN_NOT_ZERO"
    else:
        classification = "METRIC_POSITIVE" if refused > 0 else "METRIC_ZERO_OBSERVED_WINDOW"
    value = {"status": "COLLECTOR_SELF_TELEMETRY_OBSERVABLE_REFUSALS_ACTIVE"
        if current and refused and refused > 0 else "COLLECTOR_SELF_TELEMETRY_INCOMPLETE"
        if not current else "COLLECTOR_SELF_TELEMETRY_OBSERVABLE",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "current_pod": full["pod_name"], "pod_uid": full["pod_uid"],
        "pod_restarts": full["pod_restarts"],
        "configuration": full["memory_limiter_configuration"],
        "self_telemetry_delivery": "OTLP_HTTP_PERIODIC_TO_COLLECTOR_4318",
        "direct_8888_scrape_configured": full["direct_8888_scrape_configured"],
        "matching_prometheus_scrape_targets": full["matching_prometheus_targets"],
        "limiter_refusal_classification": classification,
        "limiter_observed_first": metrics["limiter_refused"]["first"],
        "limiter_observed_last": metrics["limiter_refused"]["last"],
        "limiter_observed_counter_delta": refused,
        "receiver_grpc_refused_counter_delta": metrics[
            "receiver_refused_grpc"]["observed_counter_delta"],
        "receiver_http_refused_counter_delta": metrics[
            "receiver_refused_http"]["observed_counter_delta"],
        "receiver_grpc_accepted_counter_delta": metrics[
            "receiver_accepted_grpc"]["observed_counter_delta"],
        "jaeger_sent_counter_delta": metrics["jaeger_sent"]["observed_counter_delta"],
        "jaeger_queue_peak_bytes": metrics["jaeger_queue_size"]["maximum"],
        "heap_peak_bytes": metrics["heap_bytes"]["maximum"],
        "exporter_failed_metric": metrics["jaeger_send_failed"]["status"],
        "exporter_enqueue_failed_metric": metrics["jaeger_enqueue_failed"]["status"],
        "current_pod_limiter_metric_supported_and_observed": metrics[
            "limiter_refused"]["status"] == "OBSERVABLE",
        "prior_candidate_limiter_metric_missing": prior[
            "limiter_refused_metric"] == "SERIES_ABSENT_UNKNOWN_NOT_ZERO",
        "prior_missing_likely_lazy_emission_not_proven_zero": True,
        "current_short_canary_limiter_observed_delta": smoke[
            "collector_counter_deltas"]["limiter_refused"]["observed_delta"],
        "current_short_canary_proxy_coverage": [smoke["proxy_server_matched"],
            smoke["issued_requests"]],
        "long_term_reliability_proven": False,
        "collector_configuration_modified_this_round": False,
        "source_sha256": {str(path): sha(path) for path in (FULL, SMOKE, PRIOR)},
        "fault_injected": False}
    OUTPUT.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
        "limiter_delta": refused, "canary_delta": value[
            "current_short_canary_limiter_observed_delta"]}))


if __name__ == "__main__":
    main()
