#!/usr/bin/env python3
"""Attribute clean-window CPU demand to stable business and Collector Pods."""

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request


PROMETHEUS = "http://10.97.14.138:9090"
QUERY = 'container_cpu_usage_seconds_total{namespace="otel-demo",container!="",container!="POD"}'


def read(path):
    return json.loads(path.read_text())


def rates(matrix):
    result = {}
    for item in matrix["data"]["result"]:
        name = item["metric"].get("pod")
        values = item["values"]
        if not name or len(values) < 2:
            continue
        start, end = values[0], values[-1]
        duration = float(end[0]) - float(start[0])
        delta = float(end[1]) - float(start[1])
        if duration > 0 and delta >= 0:
            result[name] = delta / duration
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("CPU attribution archive must be new")
    args.output.mkdir(parents=True)
    schedule = read(args.run / "load_pair/schedule.json")
    start = datetime.fromisoformat(schedule["scheduled_start_utc"])
    params = urllib.parse.urlencode({"query": QUERY,
        "start": (start - timedelta(minutes=2)).timestamp(),
        "end": (start - timedelta(seconds=1)).timestamp(), "step": 15})
    with urllib.request.urlopen(PROMETHEUS + "/api/v1/query_range?" + params,
                                timeout=30) as response:
        prior = json.load(response)
    if prior.get("status") != "success":
        raise ValueError("Pre-run per-Pod CPU series is unavailable")
    prior_path = args.output / "prometheus-prerun-pod-cpu.json"
    prior_path.write_text(json.dumps(prior, separators=(",", ":")) + "\n")
    before = rates(prior)
    run_values = {}
    source_hashes = {}
    for phase in ("before", "during", "after"):
        path = args.run / "capture-pipeline/captures" / phase / "metrics/cpu.json"
        source_hashes[phase] = hashlib.sha256(path.read_bytes()).hexdigest()
        for item in read(path)["data"]["result"]:
            pod = item["metric"].get("pod")
            if pod:
                run_values.setdefault(pod, []).extend(item["values"])
    run = rates({"data": {"result": [{"metric": {"pod": pod}, "values": values}
                     for pod, values in run_values.items()]}})
    pods = read(args.run / "capture-pipeline/captures/before/kubernetes/pods.json")
    nodes = {row["metadata"]["name"]: row["spec"].get("nodeName")
             for row in pods["items"]}
    rows = []
    for pod in sorted(set(before) & set(run)):
        rows.append({"pod": pod, "node": nodes.get(pod),
                     "prerun_cpu_cores": before[pod],
                     "window_cpu_cores": run[pod],
                     "added_cpu_cores": run[pod] - before[pod]})
    rows.sort(key=lambda row: row["added_cpu_cores"], reverse=True)
    result = {"status": "CPU_ATTRIBUTION_COMPLETE", "run_id": schedule["run_id"],
        "method": "counter_rate_full_25min_minus_counter_rate_prerun_2min",
        "reference_source": "PROMETHEUS_HISTORICAL_QUERY_NOT_LIVE_CAPTURE",
        "rows": rows, "missing_prerun_pods": sorted(set(run) - set(before)),
        "source_sha256": source_hashes,
        "prerun_sha256": hashlib.sha256(prior_path.read_bytes()).hexdigest()}
    with (args.output / "summary.json").open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "top_added": rows[:8]}))


if __name__ == "__main__":
    main()
