#!/usr/bin/env python3
"""Compare full clean-window Node CPU with a separate pre-run reference."""

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import statistics
import urllib.parse
import urllib.request

import yaml


PROMETHEUS = "http://10.97.14.138:9090"


def read(path):
    return json.loads(path.read_text())


def medians(matrix):
    result = {}
    for row in matrix["data"]["result"]:
        name = row["metric"].get("k8s_node_name")
        samples = [float(value) for _, value in row["values"]]
        if name and samples:
            result[name] = statistics.median(samples)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Resource reference archive must be new")
    args.output.mkdir(parents=True)
    plan = yaml.safe_load(args.plan.read_text())
    limit = float(plan["workload"]["max_added_node_cpu_cores"])
    schedule = read(args.run / "load_pair/schedule.json")
    start = datetime.fromisoformat(schedule["scheduled_start_utc"])
    low = (start - timedelta(minutes=2)).timestamp()
    high = (start - timedelta(seconds=1)).timestamp()
    url = PROMETHEUS + "/api/v1/query_range?" + urllib.parse.urlencode({
        "query": "k8s_node_cpu_usage", "start": low, "end": high, "step": 15})
    with urllib.request.urlopen(url, timeout=30) as response:
        prior = json.load(response)
    if prior.get("status") != "success":
        raise ValueError("Pre-run Node CPU reference is unavailable")
    prior_path = args.output / "prometheus-prerun-reference.json"
    prior_path.write_text(json.dumps(prior, separators=(",", ":")) + "\n")
    reference = medians(prior)
    run_values = {}
    hashes = {}
    for phase in ("before", "during", "after"):
        path = args.run / "capture-pipeline/captures" / phase / "metrics/node_cpu.json"
        hashes[phase] = hashlib.sha256(path.read_bytes()).hexdigest()
        matrix = read(path)
        for row in matrix["data"]["result"]:
            name = row["metric"].get("k8s_node_name")
            if name:
                run_values.setdefault(name, []).extend(float(value) for _, value in
                                                        row["values"])
    full = {name: statistics.median(values) for name, values in run_values.items()
            if values}
    expected = {"k8s-master", "k8s-worker1", "k8s-worker2", "k8s-worker3"}
    if set(reference) != expected or set(full) != expected:
        raise ValueError("Four-Node resource reference is incomplete")
    delta = {name: round(full[name] - reference[name], 6) for name in expected}
    status = "RESOURCE_PRESSURE_PASS" if all(value <= limit for value in
                                             delta.values()) else "RESOURCE_PRESSURE_BLOCKED"
    result = {"status": status, "run_id": schedule["run_id"],
        "threshold_added_cores_max": limit, "median_prerun_cores": reference,
        "median_full_window_cores": full, "added_cores": delta,
        "reference_source": "PROMETHEUS_HISTORICAL_QUERY_NOT_LIVE_CAPTURE",
        "reference_window_utc": {"start": datetime.fromtimestamp(low, start.tzinfo).isoformat(),
                                 "end": datetime.fromtimestamp(high, start.tzinfo).isoformat()},
        "reference_sha256": hashlib.sha256(prior_path.read_bytes()).hexdigest(),
        "captured_node_cpu_sha256": hashes,
        "not_a_formal_admission_source": True}
    with (args.output / "decision.json").open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": status, "added_cores": delta}))


if __name__ == "__main__":
    main()
