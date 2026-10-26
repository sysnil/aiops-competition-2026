"""Read-only Prometheus reference-window and leaf-container CPU audit."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import statistics
import urllib.parse
import urllib.request


ROOT = Path("/root/stage5-v3-clean-gate-v2")
PROM = "http://10.97.14.138:9090"
RUNS = ("VAL-1", "VAL-2")
BUSINESS = {"frontend", "frontend-proxy", "product-catalog", "recommendation",
            "ad", "cart", "checkout", "currency", "payment", "shipping",
            "email", "quote", "fraud-detection", "accounting", "image-provider"}


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def query(expression, start, end, step):
    url = PROM + "/api/v1/query_range?" + urllib.parse.urlencode({
        "query": expression, "start": start, "end": end, "step": step})
    with urllib.request.urlopen(url, timeout=60) as response:
        value = json.load(response)
    if value.get("status") != "success" or value["data"]["resultType"] != "matrix":
        raise ValueError("Prometheus range query failed")
    return value


def points(row):
    return [(float(t), float(value)) for t, value in row["values"]]


def segment(values, lo, hi):
    return [value for t, value in values if lo <= t < hi]


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)]


def kind(metric):
    namespace = metric.get("namespace", "")
    container = metric.get("container", "")
    if namespace == "otel-demo" and container in BUSINESS:
        return "business"
    if namespace in ("otel-demo", "calico-system", "monitoring", "kube-system") and (
            "otel" in container or "collector" in container or
            container in ("calico-node", "prometheus", "jaeger", "opensearch")):
        return "observability"
    return "other_container"


def main():
    output = ROOT / "results/cpu-reference-analysis.json"
    if output.exists():
        raise ValueError("Refusing overwrite")
    results = []
    sources = {}
    for run in RUNS:
        decision_path = ROOT / "snapshots/reference" / (run +
            "-resource-decision.json")
        decision = read(decision_path)
        sources[str(decision_path)] = sha(decision_path)
        start = datetime.fromisoformat(decision["reference_window_utc"][
            "end"]) + timedelta(seconds=1)
        before = start.timestamp()
        end = before + 1500
        node = query('k8s_node_cpu_usage{k8s_node_name="k8s-worker3"}',
                     before - 600, end, 15)
        if len(node["data"]["result"]) != 1:
            raise ValueError("Worker3 CPU series absent or ambiguous")
        node_path = ROOT / "snapshots" / (run + "-node-cpu-query.json")
        with node_path.open("x") as stream:
            json.dump(node, stream, separators=(",", ":"))
            stream.write("\n")
        series = points(node["data"]["result"][0])
        full = segment(series, before, end)
        if len(full) < 90:
            raise ValueError("Full Worker3 CPU range incomplete")
        refs = {}
        for minutes in (2, 5, 10):
            values = segment(series, before - minutes * 60, before)
            if len(values) < minutes * 3:
                raise ValueError("CPU reference window too short")
            refs[str(minutes)] = {"samples": len(values),
                "median_cores": statistics.median(values),
                "delta_full_median_cores": statistics.median(full) -
                    statistics.median(values)}
        pod_expression = ('sum by(namespace,container,pod) (rate('
            'container_cpu_usage_seconds_total{instance="k8s-worker3",'
            'container!="",container!="POD"}[1m]))')
        pods = query(pod_expression, before - 600, end, 30)
        pod_path = ROOT / "snapshots" / (run + "-pod-cpu-query.json")
        with pod_path.open("x") as stream:
            json.dump(pods, stream, separators=(",", ":"))
            stream.write("\n")
        attribution = {name: 0.0 for name in
                       ("business", "observability", "other_container")}
        labels = []
        for row in pods["data"]["result"]:
            values = segment(points(row), before, end)
            if values:
                category = kind(row["metric"])
                attribution[category] += statistics.mean(values)
                labels.append({"category": category, "metric": row["metric"],
                               "mean_cores": statistics.mean(values),
                               "samples": len(values)})
        node_mean = statistics.mean(full)
        results.append({"run": run, "start_utc": start.isoformat(),
            "reference_cores": refs, "old_recorded_2_minute_reference":
                decision["median_prerun_cores"]["k8s-worker3"],
            "node_full_median_cores": statistics.median(full),
            "node_full_mean_cores": node_mean,
            "node_full_max_cores": max(full),
            "node_full_p95_cores": percentile(full, .95),
            "node_full_max_fraction_of_8_cores": max(full) / 8,
            "leaf_container_mean_cores": attribution,
            "unattributed_node_minus_leaf_mean_cores": node_mean -
                sum(attribution.values()),
            "leaf_series": labels,
            "host_user_system_iowait_steal_during_run": "NOT_ARCHIVED",
            "node_psi_during_run": "NOT_ARCHIVED",
            "source_sha256": {str(node_path): sha(node_path),
                              str(pod_path): sha(pod_path)}})
    result = {"status": "EXPLORATORY_REFERENCE_SENSITIVITY",
        "at_utc": datetime.now(timezone.utc).isoformat(), "runs": results,
        "source_sha256": sources, "historical_old_gate_reclassified": False,
        "attribution_caveat": "Leaf container rates and node usage are descriptive; host processes overlap containers and remain unattributed where absent"}
    with output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "runs": [{
        "run": row["run"], "reference_deltas": {k: round(v[
            "delta_full_median_cores"], 4) for k, v in row[
            "reference_cores"].items()},
        "max_cores": round(row["node_full_max_cores"], 4)}
        for row in results]}))


if __name__ == "__main__":
    main()
