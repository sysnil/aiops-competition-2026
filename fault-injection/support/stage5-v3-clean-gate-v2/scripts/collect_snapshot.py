"""Read-only live cluster snapshot; all uncertain observations remain UNKNOWN."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request


PROM = "http://10.97.14.138:9090"
OPENSEARCH = "http://10.111.114.255:9200"
JAEGER = "http://10.107.96.1:16686/jaeger/ui/api"
NODES = {"k8s-master", "k8s-worker1", "k8s-worker2", "k8s-worker3"}
METRICS = ("k8s_node_cpu_usage", "k8s_node_memory_available_bytes",
           "k8s_node_condition_ready")


def command(*args, timeout=30):
    return subprocess.check_output(args, text=True, timeout=timeout)


def get_json(url):
    with urllib.request.urlopen(url, timeout=15) as response:
        return json.load(response)


def component_probe(fn):
    try:
        return "PASS" if fn() else "FAIL"
    except Exception:
        return "UNKNOWN"


def kube_pods_ready(prefix):
    pods = json.loads(command("kubectl", "-n", "otel-demo", "get", "pods",
                              "-o", "json"))["items"]
    matches = [pod for pod in pods if pod["metadata"]["name"].startswith(prefix)]
    return bool(matches) and all(pod["status"].get("phase") == "Running" and
        all(item.get("ready") for item in pod["status"].get("containerStatuses", []))
        for pod in matches)


def prom(expression, endpoint="query"):
    return get_json(PROM + "/api/v1/" + endpoint + "?" +
                    urllib.parse.urlencode({"query": expression}))


def metrics_window(now):
    end = int(now.timestamp() // 30 * 30)
    data = {}
    for name in METRICS:
        url = PROM + "/api/v1/query_range?" + urllib.parse.urlencode({
            "query": name, "start": end - 600, "end": end, "step": 30})
        try:
            result = get_json(url)
            rows = {}
            if result["status"] != "success":
                raise ValueError("Prometheus query failed")
            for item in result["data"]["result"]:
                node = item["metric"].get("k8s_node_name") or item["metric"].get("node")
                samples = item.get("values", [])
                if node in NODES and samples:
                    rows[node] = {"sample_count": len(samples),
                        "window_seconds": samples[-1][0] - samples[0][0],
                        "latest_sample_utc": datetime.fromtimestamp(
                            samples[-1][0], timezone.utc).isoformat()}
            data[name] = rows
        except Exception as error:
            data[name] = {"query_error": type(error).__name__}
    return data


def trace_storage_ready(now):
    body = json.dumps({"query": {"bool": {"filter": [
        {"range": {"startTimeMillis": {"gte": int(now.timestamp() * 1000) -
            120000, "lt": int(now.timestamp() * 1000)}}},
        {"term": {"process.serviceName": "frontend"}}]}}}).encode()
    request = urllib.request.Request(
        OPENSEARCH + "/jaeger-jaeger-span-*/_count", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response).get("count", 0) > 0


def bgp_ready():
    pods = json.loads(command("kubectl", "-n", "calico-system", "get",
                              "pods", "-l", "k8s-app=calico-node", "-o", "json"))["items"]
    if len(pods) != 4:
        return False
    return all(re.search(r"established = 3", command("kubectl", "-n",
        "calico-system", "exec", pod["metadata"]["name"], "-c",
        "calico-node", "--", "/bin/calico-node", "-bird-ready")) for pod in pods)


def running_pod_identities():
    pods = json.loads(command("kubectl", "get", "pods", "-A", "-o", "json"))["items"]
    return {pod["metadata"]["namespace"] + "/" + pod["metadata"]["name"]:
            {"uid": pod["metadata"]["uid"],
             "restarts": sum(row.get("restartCount", 0) for row in
                             pod["status"].get("containerStatuses", []))}
            for pod in pods if pod["status"].get("phase") == "Running"}


def collect():
    now = datetime.now(timezone.utc)
    raw_nodes = json.loads(command("kubectl", "get", "nodes", "-o", "json"))
    nodes = {}
    for item in raw_nodes["items"]:
        name = item["metadata"]["name"]
        conditions = {row["type"]: row for row in item["status"]["conditions"]}
        nodes[name] = {kind: conditions.get(kind, {}).get("status", "Unknown")
                       for kind in ("Ready", "DiskPressure", "MemoryPressure", "PIDPressure")}
        nodes[name]["condition_heartbeat_utc"] = conditions.get("Ready", {}).get(
            "lastHeartbeatTime")
        nodes[name]["disk_pressure_taint"] = any(t.get("key") ==
            "node.kubernetes.io/disk-pressure" for t in item.get("spec", {}).get("taints", []))
    leases = json.loads(command("kubectl", "-n", "kube-node-lease", "get",
                                "lease", "-o", "json"))["items"]
    for lease in leases:
        name = lease["metadata"]["name"]
        if name in nodes:
            nodes[name]["node_lease_renew_utc"] = lease["spec"].get("renewTime")
    ds = json.loads(command("kubectl", "-n", "otel-demo", "get", "ds",
                            "otel-infra-agent-agent", "-o", "json"))["status"]
    summary = json.loads(command("kubectl", "get", "--raw",
        "/api/v1/nodes/k8s-master/proxy/stats/summary"))
    config = json.loads(command("kubectl", "get", "--raw",
        "/api/v1/nodes/k8s-master/proxy/configz"))["kubeletconfig"]
    fs = summary["node"]["fs"]
    imagefs = summary["node"]["runtime"]["imageFs"]
    storage = {"available_bytes": fs.get("availableBytes"),
        "capacity_bytes": fs.get("capacityBytes"),
        "inodes_free_fraction": fs["inodesFree"] / fs["inodes"],
        "imagefs_available_fraction": imagefs["availableBytes"] /
            imagefs["capacityBytes"],
        "imagefs_used_bytes": imagefs.get("usedBytes"),
        "nodefs_used_bytes": fs.get("usedBytes"),
        "kubelet_eviction_hard": config.get("evictionHard"),
        "image_gc_high_percent": config.get("imageGCHighThresholdPercent"),
        "image_gc_low_percent": config.get("imageGCLowThresholdPercent")}
    components = {
        "prometheus": component_probe(lambda: urllib.request.urlopen(
            PROM + "/-/ready", timeout=10).status == 200),
        "opensearch": component_probe(lambda: get_json(
            OPENSEARCH + "/_cluster/health").get("status") == "green"),
        "jaeger": component_probe(lambda: "frontend" in get_json(
            JAEGER + "/services").get("data", [])),
        "collector": component_probe(lambda: kube_pods_ready("otel-collector-")),
        "cluster_collector": component_probe(lambda: kube_pods_ready(
            "otel-cluster-collector-")),
        "trace_storage": component_probe(lambda: trace_storage_ready(now)),
        "tigera_operator": component_probe(lambda: (
            lambda deployment: deployment["status"].get("readyReplicas", 0) ==
            deployment["spec"].get("replicas", 1))(
                json.loads(command("kubectl", "-n", "tigera-operator", "get",
                                   "deployment", "tigera-operator", "-o", "json")))),
        "calico_bgp": component_probe(bgp_ready), "workload_http": "UNKNOWN",
        "five_host_residue": "UNKNOWN", "independent_watchdog": "UNKNOWN",
        "no_active_fault": "UNKNOWN", "no_mass_pod_restart": "UNKNOWN",
        "network_connectivity": component_probe(lambda: all(
            subprocess.run(["ping", "-c", "2", "-W", "2", address],
                           capture_output=True, timeout=6).returncode == 0
            for address in ("192.168.53.53", "192.168.53.54",
                            "192.168.53.55", "192.168.53.50")))}
    try:
        rows = prom('sum(increase(k6_http_req_failed_total{service_name="load-generator",'
                    'condition="nonzero"}[2m])) or vector(0)')["data"]["result"]
        components["workload_http"] = "PASS" if len(rows) == 1 and float(
            rows[0]["value"][1]) == 0 else "FAIL"
    except Exception:
        pass
    return {"captured_at_utc": now.isoformat(), "nodes": nodes,
            "infra_agent": {"desired": ds.get("desiredNumberScheduled"),
                            "ready": ds.get("numberReady"),
                            "available": ds.get("numberAvailable")},
            "metrics": metrics_window(now), "storage": storage,
            "components": components, "sources": {
            "kubernetes": "kubectl API", "prometheus": PROM,
            "kubelet": "node/stats/summary and configz",
            "opensearch": OPENSEARCH, "jaeger": JAEGER},
            "running_pod_identities": running_pod_identities(),
            "read_only": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = collect()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"captured_at_utc": value["captured_at_utc"],
                      "disk_pressure": value["nodes"].get("k8s-master", {}).get(
                          "DiskPressure"), "infra": value["infra_agent"]}))


if __name__ == "__main__":
    main()
