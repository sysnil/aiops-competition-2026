#!/usr/bin/env python3
"""Build the live OTLP/Jaeger route from structured Kubernetes configuration."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import yaml


def kube(kind, name=None):
    command = ["kubectl", "-n", "otel-demo", "get", kind]
    if name:
        command.append(name)
    command.extend(("-o", "json"))
    return json.loads(subprocess.check_output(command, text=True, timeout=35))


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    if args.raw_output.exists() or args.result.exists():
        raise ValueError("Refusing overwrite")
    args.raw_output.mkdir(parents=True)
    resources = {
        "pods": kube("pods"), "deployments": kube("deployments"),
        "services": kube("services"),
        "gateway-config": kube("configmap", "otel-collector"),
        "cluster-config": kube("configmap", "otel-cluster-collector"),
        "infra-config": kube("configmap", "otel-infra-agent-agent"),
        "proxy-config": kube("configmap", "s5v3ap-frontend-proxy-idle-c2"),
        "jaeger-config": kube("configmap", "user-config"),
    }
    hashes = {}
    for name, value in resources.items():
        path = args.raw_output / (name + ".json")
        path.write_text(json.dumps(value, indent=2) + "\n")
        hashes[name] = sha_bytes(path.read_bytes())
    pods = {item["metadata"]["name"]: item for item in resources["pods"]["items"]}
    roles = {}
    for role, prefix in (("gateway", "otel-collector-"),
                         ("cluster", "otel-cluster-collector-"),
                         ("proxy", "frontend-proxy-"), ("jaeger", "jaeger-"),
                         ("opensearch", "opensearch-")):
        rows = [item for name, item in pods.items() if name.startswith(prefix)]
        if len(rows) != 1:
            raise ValueError("Role Pod not uniquely identified: " + role)
        pod = rows[0]
        roles[role] = {"pod": pod["metadata"]["name"],
            "uid": pod["metadata"]["uid"], "ip": pod["status"].get("podIP"),
            "node": pod["spec"].get("nodeName")}
    gateway = yaml.safe_load(resources["gateway-config"]["data"]["relay"])
    cluster = yaml.safe_load(resources["cluster-config"]["data"]["relay"])
    infra = yaml.safe_load(resources["infra-config"]["data"]["relay"])
    jaeger = yaml.safe_load(resources["jaeger-config"]["data"]["user-config.yaml"])
    proxy = yaml.safe_load(resources["proxy-config"]["data"]["envoy.tmpl.yaml"])
    business = {}
    for deployment in resources["deployments"]["items"]:
        name = deployment["metadata"]["name"]
        if name in ("otel-collector", "otel-cluster-collector", "jaeger"):
            continue
        env = {row["name"]: row.get("value") for container in deployment[
            "spec"]["template"]["spec"]["containers"] for row in container.get("env", [])}
        endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if endpoint and ("otel-collector" in endpoint or (
                env.get("OTEL_COLLECTOR_NAME") == "otel-collector" and
                "$(OTEL_COLLECTOR_NAME)" in endpoint)):
            business[name] = {"endpoint_template": endpoint,
                "protocol_explicit": env.get("OTEL_EXPORTER_OTLP_PROTOCOL"),
                "protocol_if_not_explicit": "SDK_DEFAULT_NOT_ASSUMED"}
    gateway_traces = gateway["service"]["pipelines"]["traces"]
    if ("traces" in cluster["service"]["pipelines"] or
            "traces" in infra["service"]["pipelines"] or
            "otlp" not in gateway_traces["receivers"] or
            gateway_traces["processors"][0] != "memory_limiter"):
        raise ValueError("Trace pipeline differs from audited route")
    exporter = gateway["exporters"]["otlp_grpc/jaeger"]
    cluster_exporter = cluster["exporters"]["otlp/main"]
    infra_exporter = infra["exporters"]["otlp/main"]
    backend = jaeger["extensions"]["jaeger_storage"]["backends"]["opensearch_backend"]
    value = {"status": "LIVE_COMPONENT_TRACE_PIPELINE_AUDITED",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_kubernetes_sha256": hashes,
        "components": roles, "business_sdk_endpoints": business,
        "frontend_proxy": {"tracing_provider": "envoy.tracers.opentelemetry",
            "otlp_cluster": "opentelemetry_collector_grpc",
            "collector_port": 4317,
            "source_configmap_sha256": hashes["proxy-config"]},
        "gateway": {"service": "otel-collector", "grpc_otlp_port": 4317,
            "http_otlp_port": 4318, "trace_pipeline": gateway_traces,
            "memory_limiter": gateway["processors"]["memory_limiter"],
            "batch": gateway["processors"]["batch"],
            "jaeger_exporter": exporter,
            "gateway_storage_file_exporter": gateway["exporters"]["file/traces"]},
        "cluster_collector": {"source_pipelines": list(cluster["service"]["pipelines"]),
            "traces_pipeline_present": False, "exporter": cluster_exporter},
        "infra_agent": {"source_pipelines": list(infra["service"]["pipelines"]),
            "traces_pipeline_present": False, "exporter": infra_exporter},
        "jaeger": {"otlp_grpc_port": 4317, "trace_pipeline": jaeger["service"][
            "pipelines"]["traces"], "storage_backend": "opensearch_backend",
            "storage_server_urls": backend["elasticsearch"]["server_urls"]},
        "raw_otlp_max_message_size_explicitly_configured": False,
        "default_otlp_max_message_size_not_assumed": True,
        "private_gt_read": False}
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "gateway": roles["gateway"],
                      "business_sdk_services": len(business)}))


if __name__ == "__main__":
    main()
