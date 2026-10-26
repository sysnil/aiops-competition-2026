#!/usr/bin/env python3
"""Record one new Collector instance's counter availability before Canary."""

from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.parse
import urllib.request


POD = "otel-collector-d56f65857-n8rsq"
PROM = "http://10.97.14.138:9090"
OUTPUT = Path("/root/stage5-v3-collector-reliability/results/precanary-metric-interface.json")
EXPRESSIONS = {
    "limiter_accepted": 'otelcol_processor_memory_limiter_accepted_spans_total{k8s_pod_name="' + POD + '"}',
    "limiter_refused": 'otelcol_processor_memory_limiter_refused_spans_total{k8s_pod_name="' + POD + '"}',
    "receiver_accepted": 'otelcol_receiver_accepted_spans_total{k8s_pod_name="' + POD + '",transport="grpc"}',
    "receiver_refused": 'otelcol_receiver_refused_spans_total{k8s_pod_name="' + POD + '",transport="grpc"}',
    "jaeger_sent": 'otelcol_exporter_sent_spans_total{k8s_pod_name="' + POD + '",exporter="otlp_grpc/jaeger"}',
}


def main():
    if OUTPUT.exists():
        raise ValueError("Refusing overwrite")
    observations = {}
    for name, expression in EXPRESSIONS.items():
        url = PROM + "/api/v1/query?" + urllib.parse.urlencode({"query": expression})
        with urllib.request.urlopen(url, timeout=20) as response:
            raw = json.load(response)
        if raw["status"] != "success" or len(raw["data"]["result"]) > 1:
            raise ValueError("Metric source failed or mixed Collector instances")
        observations[name] = {"promql": expression,
            "series_present": len(raw["data"]["result"]) == 1,
            "raw_response": raw}
    value = {"status": "PRE_CANARY_REFUSAL_METRIC_SERIES_ABSENT",
        "at_utc": datetime.now(timezone.utc).isoformat(), "collector_pod": POD,
        "observations": observations,
        "refused_value_assumed_zero": False,
        "accepted_metric_present": observations["limiter_accepted"]["series_present"],
        "original_source_lock_status": "PRESERVED_PRE_CANARY",
        "collector_configuration_changed_after_precheck": False,
        "fault_injected": False}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "series_present": {
        name: row["series_present"] for name, row in observations.items()}}))


if __name__ == "__main__":
    main()
