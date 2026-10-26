#!/usr/bin/env python3
"""Read-only Collector pipeline, Kubernetes and Prometheus provenance audit."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request


PROM = "http://10.97.14.138:9090"
RESOURCES = {
    "pods": ["kubectl", "-n", "otel-demo", "get", "pods", "-o", "json"],
    "services": ["kubectl", "-n", "otel-demo", "get", "services", "-o", "json"],
    "endpointslices": ["kubectl", "-n", "otel-demo", "get", "endpointslices", "-o", "json"],
    "gateway-deployment": ["kubectl", "-n", "otel-demo", "get", "deployment", "otel-collector", "-o", "json"],
    "gateway-configmap": ["kubectl", "-n", "otel-demo", "get", "configmap", "otel-collector", "-o", "json"],
    "cluster-deployment": ["kubectl", "-n", "otel-demo", "get", "deployment", "otel-cluster-collector", "-o", "json"],
    "cluster-configmap": ["kubectl", "-n", "otel-demo", "get", "configmap", "otel-cluster-collector", "-o", "json"],
    "infra-daemonset": ["kubectl", "-n", "otel-demo", "get", "daemonset", "otel-infra-agent-agent", "-o", "json"],
    "proxy-deployment": ["kubectl", "-n", "otel-demo", "get", "deployment", "frontend-proxy", "-o", "json"],
    "jaeger-deployment": ["kubectl", "-n", "otel-demo", "get", "deployment", "jaeger", "-o", "json"],
}
METRICS = {
    "gateway_receiver_accepted_grpc": 'sum(otelcol_receiver_accepted_spans_total{service_name="otelcol-contrib",transport="grpc"})',
    "gateway_receiver_refused_grpc": 'sum(otelcol_receiver_refused_spans_total{service_name="otelcol-contrib",transport="grpc"})',
    "gateway_receiver_failed_grpc": 'sum(otelcol_receiver_failed_spans_total{service_name="otelcol-contrib",transport="grpc"})',
    "gateway_limiter_accepted_spans": 'sum(otelcol_processor_memory_limiter_accepted_spans_total{service_name="otelcol-contrib"})',
    "gateway_limiter_refused_spans": 'sum(otelcol_processor_memory_limiter_refused_spans_total{service_name="otelcol-contrib"})',
    "gateway_limiter_refused_logs": 'sum(otelcol_processor_memory_limiter_refused_log_records_total{service_name="otelcol-contrib"})',
    "gateway_limiter_refused_metrics": 'sum(otelcol_processor_memory_limiter_refused_metric_points_total{service_name="otelcol-contrib"})',
    "gateway_jaeger_sent_spans": 'sum(otelcol_exporter_sent_spans_total{service_name="otelcol-contrib",exporter="otlp_grpc/jaeger"})',
    "gateway_jaeger_queue_size": 'sum(otelcol_exporter_queue_size{service_name="otelcol-contrib",exporter="otlp_grpc/jaeger"})',
    "gateway_jaeger_queue_capacity": 'sum(otelcol_exporter_queue_capacity{service_name="otelcol-contrib",exporter="otlp_grpc/jaeger"})',
    "gateway_jaeger_in_flight": 'sum(otelcol_exporter_in_flight_requests{service_name="otelcol-contrib",exporter="otlp_grpc/jaeger"})',
    "gateway_heap_bytes": 'sum(otelcol_process_runtime_heap_alloc_bytes{service_name="otelcol-contrib"})',
    "gateway_rss_bytes": 'sum(otelcol_process_memory_rss_bytes{service_name="otelcol-contrib"})',
    "gateway_container_working_set_bytes": 'sum(container_memory_working_set_bytes{namespace="otel-demo",pod=~"otel-collector-.*",container="opentelemetry-collector"})',
    "gateway_container_cpu_cores": 'sum(rate(container_cpu_usage_seconds_total{namespace="otel-demo",pod=~"otel-collector-.*",container="opentelemetry-collector"}[1m]))',
    "jaeger_receiver_accepted_grpc": 'sum(otelcol_receiver_accepted_spans_total{service_name="jaeger",transport="grpc"})',
    "jaeger_receiver_refused_grpc": 'sum(otelcol_receiver_refused_spans_total{service_name="jaeger",transport="grpc"})',
}
ABSENT_CHECKS = {
    "gateway_exporter_send_failed_spans": 'otelcol_exporter_send_failed_spans_total{service_name="otelcol-contrib",exporter="otlp_grpc/jaeger"}',
    "gateway_exporter_retries": 'otelcol_exporter_enqueue_failed_spans_total{service_name="otelcol-contrib",exporter="otlp_grpc/jaeger"}',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request_json(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def query_range(expression, start, end, step):
    url = PROM + "/api/v1/query_range?" + urllib.parse.urlencode({
        "query": expression, "start": start, "end": end, "step": step})
    value = request_json(url)
    if value.get("status") != "success":
        raise RuntimeError("Prometheus query failed: " + expression)
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite")
    args.output.mkdir(parents=True)
    resources = {}
    hashes = {}
    for name, command in RESOURCES.items():
        raw = subprocess.check_output(command, text=True, timeout=35)
        path = args.output / "kubernetes" / (name + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw if raw.endswith("\n") else raw + "\n")
        resources[name] = json.loads(raw)
        hashes[name] = sha(path)
    now = datetime.now(timezone.utc)
    windows = {
        "historical_gap": (datetime(2026, 10, 12, 10, 44, 55, tzinfo=timezone.utc),
                           datetime(2026, 10, 12, 10, 46, 10, tzinfo=timezone.utc), 5),
        "recent_2h": (now - timedelta(hours=2), now - timedelta(seconds=15), 10),
    }
    metric_manifest = {}
    for window, (start, end, step) in windows.items():
        metric_manifest[window] = {"start_utc": start.isoformat(),
            "end_utc": end.isoformat(), "step_seconds": step, "queries": {}}
        for name, expression in {**METRICS, **ABSENT_CHECKS}.items():
            raw = query_range(expression, start.timestamp(), end.timestamp(), step)
            path = args.output / "prometheus" / window / (name + ".json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(raw) + "\n")
            metric_manifest[window]["queries"][name] = {"promql": expression,
                "sha256": sha(path), "series_count": len(raw["data"]["result"]),
                "sample_count": sum(len(row.get("values", [])) for row in raw["data"]["result"])}
    pods = {item["metadata"]["name"]: item for item in resources["pods"]["items"]}
    roles = {}
    for name, prefix in (("gateway", "otel-collector-"),
                         ("cluster", "otel-cluster-collector-"),
                         ("proxy", "frontend-proxy-"), ("jaeger", "jaeger-")):
        matched = [item for pod, item in pods.items() if pod.startswith(prefix)]
        if len(matched) != 1:
            raise ValueError("Role Pod not uniquely identified: " + name)
        item = matched[0]
        roles[name] = {"pod": item["metadata"]["name"], "uid": item["metadata"]["uid"],
            "ip": item["status"].get("podIP"), "node": item["spec"].get("nodeName"),
            "containers": [{"name": container["name"], "image": container["image"],
                "resources": container.get("resources"),
                "runtime_image_id": next((row.get("imageID") for row in item["status"].get(
                    "containerStatuses", []) if row.get("name") == container["name"]), None),
                "restart_count": next((row.get("restartCount") for row in item["status"].get(
                    "containerStatuses", []) if row.get("name") == container["name"]), None)}
                for container in item["spec"]["containers"]]}
    value = {"status": "READ_ONLY_PIPELINE_SNAPSHOT", "captured_at_utc": now.isoformat(),
        "kubernetes_source_sha256": hashes, "roles": roles, "metrics": metric_manifest,
        "explicitly_missing_metric_series_are_unknown_not_zero": True,
        "private_gt_read": False, "fault_injected": False}
    (args.output / "snapshot-manifest.json").write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "roles": {key: row["pod"] for key, row in roles.items()},
                      "metric_files": sum(len(row["queries"]) for row in metric_manifest.values())}))


if __name__ == "__main__":
    main()
