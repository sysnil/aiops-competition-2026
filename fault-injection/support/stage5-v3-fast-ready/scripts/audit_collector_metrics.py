#!/usr/bin/env python3
"""Read current gateway self-telemetry with real sample timestamps."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request

import yaml


PROM = "http://10.97.14.138:9090"
METRICS = {
    "receiver_accepted_grpc": 'otelcol_receiver_accepted_spans_total{receiver="otlp",transport="grpc"}',
    "receiver_refused_grpc": 'otelcol_receiver_refused_spans_total{receiver="otlp",transport="grpc"}',
    "receiver_refused_http": 'otelcol_receiver_refused_spans_total{receiver="otlp",transport="http"}',
    "limiter_accepted": 'otelcol_processor_memory_limiter_accepted_spans_total{processor="memory_limiter"}',
    "limiter_refused": 'otelcol_processor_memory_limiter_refused_spans_total{processor="memory_limiter"}',
    "jaeger_sent": 'otelcol_exporter_sent_spans_total{exporter="otlp_grpc/jaeger"}',
    "jaeger_send_failed": 'otelcol_exporter_send_failed_spans_total{exporter="otlp_grpc/jaeger"}',
    "jaeger_enqueue_failed": 'otelcol_exporter_enqueue_failed_spans_total{exporter="otlp_grpc/jaeger"}',
    "jaeger_queue_size": 'otelcol_exporter_queue_size{exporter="otlp_grpc/jaeger",data_type="traces"}',
    "jaeger_queue_capacity": 'otelcol_exporter_queue_capacity{exporter="otlp_grpc/jaeger",data_type="traces"}',
    "heap_bytes": "otelcol_process_runtime_heap_alloc_bytes",
}
COUNTERS = {name for name in METRICS if name.startswith(("receiver_", "limiter_", "jaeger_sent",
                                                         "jaeger_send_failed", "jaeger_enqueue_failed"))}


def get_json(url):
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.load(response)


def prom(expression, start, end):
    url = PROM + "/api/v1/query_range?" + urllib.parse.urlencode({
        "query": expression, "start": start, "end": end, "step": 15})
    data = get_json(url)
    if data.get("status") != "success":
        raise RuntimeError("Prometheus query failed: " + expression)
    return data["data"]["result"]


def kube(*args):
    return subprocess.check_output(["/usr/bin/kubectl", "--kubeconfig=/etc/kubernetes/admin.conf",
        "--context=kubernetes-admin@kubernetes", "-n", "otel-demo", *args],
        text=True, timeout=35)


def summarize(name, expression, pod, start, end):
    selector = expression[:-1] + ',k8s_pod_name="' + pod + '"}' if expression.endswith("}") else (
        expression + '{k8s_pod_name="' + pod + '"}')
    values, stamps = prom(selector, start, end), prom("timestamp(" + selector + ")", start, end)
    if not values:
        return {"status": "NOT_EXPOSED_IN_THIS_WINDOW_UNKNOWN_NOT_ZERO",
                "expression": selector, "samples": [], "observed_counter_delta": None}
    if len(values) != 1 or len(stamps) != 1 or len(values[0]["values"]) != len(
            stamps[0]["values"]):
        return {"status": "UNKNOWN_AMBIGUOUS_SERIES",
                "expression": selector, "series_count": len(values),
                "sample_count": 0, "observed_counter_delta": None}
    raw = {}
    for (eval_ts, number), (stamp_eval, actual_ts) in zip(values[0]["values"],
                                                            stamps[0]["values"]):
        if eval_ts != stamp_eval:
            raise ValueError("Evaluation grid differs")
        actual, value = float(actual_ts), float(number)
        if actual in raw and raw[actual] != value:
            raise ValueError("Conflicting raw Collector sample")
        raw[actual] = value
    samples = [{"scrape_utc": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                "value": value} for stamp, value in sorted(raw.items())]
    vals = [item["value"] for item in samples]
    resets = sum(after < before for before, after in zip(vals, vals[1:])) if name in COUNTERS else None
    latest_age = end - max(raw)
    return {"status": "OBSERVABLE" if latest_age <= 60 else "METRIC_STALE",
            "expression": selector, "labels": values[0]["metric"],
            "sample_count": len(samples), "latest_scrape_age_seconds": latest_age,
            "first": vals[0], "last": vals[-1], "minimum": min(vals), "maximum": max(vals),
            "counter_resets": resets,
            "observed_counter_delta": vals[-1] - vals[0] if name in COUNTERS and resets == 0
                                      else None,
            "samples": samples}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    pods = json.loads(kube("get", "pods", "-o", "json"))["items"]
    matches = [pod for pod in pods if pod["metadata"]["name"].startswith("otel-collector-")
        and pod["status"].get("phase") == "Running"]
    if len(matches) != 1:
        raise ValueError("Ambiguous gateway Pod")
    pod = matches[0]
    name, uid = pod["metadata"]["name"], pod["metadata"]["uid"]
    start = int(datetime.fromisoformat(pod["status"]["startTime"].replace("Z", "+00:00")).timestamp())
    end = int(time.time())
    config = json.loads(kube("get", "configmap", "otel-collector", "-o", "json"))
    relay = yaml.safe_load(config["data"]["relay"])
    readers = relay["service"]["telemetry"]["metrics"].get("readers", [])
    metrics = {key: summarize(key, expression, name, start, end)
               for key, expression in METRICS.items()}
    targets = get_json(PROM + "/api/v1/targets")["data"].get("activeTargets", [])
    matching_targets = [{"scrapeUrl": row.get("scrapeUrl"),
        "health": row.get("health"), "labels": row.get("labels")}
        for row in targets if name in json.dumps(row) or pod["status"]["podIP"] in
        json.dumps(row)]
    value = {"status": "COLLECTOR_SELF_TELEMETRY_AUDITED",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "window_start_utc": datetime.fromtimestamp(start, timezone.utc).isoformat(),
        "window_end_utc": datetime.fromtimestamp(end, timezone.utc).isoformat(),
        "pod_name": name, "pod_uid": uid, "pod_ip": pod["status"]["podIP"],
        "pod_restarts": pod["status"]["containerStatuses"][0]["restartCount"],
        "memory_limiter_configuration": relay["processors"]["memory_limiter"],
        "self_telemetry_readers": readers,
        "direct_8888_scrape_configured": any("8888" in json.dumps(reader) for reader in readers),
        "matching_prometheus_targets": matching_targets,
        "metrics": metrics,
        "missing_series_interpreted_as_zero": False,
        "fault_injected": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "pod": name,
        "limiter": metrics["limiter_refused"]["status"],
        "limiter_delta": metrics["limiter_refused"]["observed_counter_delta"],
        "receiver_grpc_delta": metrics["receiver_refused_grpc"]["observed_counter_delta"]}))


if __name__ == "__main__":
    main()
