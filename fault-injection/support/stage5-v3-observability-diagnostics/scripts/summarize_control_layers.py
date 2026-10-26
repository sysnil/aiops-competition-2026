"""Validate old control aggregates and describe request/layer distributions."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import statistics


CONTROLS = ("image_asset", "cart_view")


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)] if ordered else None


def distribution(values):
    values = [value for value in values if isinstance(value, (int, float))]
    return {"n": len(values), "p50": percentile(values, .50),
            "p90": percentile(values, .90), "p95": percentile(values, .95),
            "p99": percentile(values, .99),
            "max": max(values) if values else None}


def block_draw(values, blocks, rng):
    result = []
    for _ in range((len(values) + blocks - 1) // blocks):
        start = rng.randrange(len(values))
        result.extend(values[(start + step) % len(values)] for step in range(blocks))
    return result[:len(values)]


def bootstrap(pair, seed, repetitions=1200):
    rng = random.Random(20261012 + seed)
    before = [[pair[name][i]["p95"] for name in CONTROLS]
              for i in range(10)]
    evaluated = [[pair[name][i]["p95"] for name in CONTROLS]
                 for i in range(10, 25)]
    maxima = []
    old_passes = 0
    for _ in range(repetitions):
        a = block_draw(before, 2, rng)
        b = block_draw(evaluated, 2, rng)
        baseline = [percentile([row[j] for row in a], .50)
                    for j in range(len(CONTROLS))]
        maximum = max(row[j] / baseline[j] for row in b
                      for j in range(len(CONTROLS)))
        maxima.append(maximum)
        old_passes += maximum <= 1.25
    return {"resampling_unit": "circular_adjacent_two_minute_blocks",
            "paired_controls_preserved": True, "repetitions": repetitions,
            "conditional_old_A_pass_fraction": old_passes / repetitions,
            "max_ratio_p05": percentile(maxima, .05),
            "max_ratio_median": percentile(maxima, .50),
            "max_ratio_p95": percentile(maxima, .95),
            "not_independent_window_confidence_interval": True}


def pearson(a, b):
    if len(a) < 3 or len(a) != len(b):
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb) ** .5 \
        if va > 0 and vb > 0 else None


def proxy_start_delta(request):
    logs = request["proxy_access"]
    servers = request["frontend_server_spans"]
    if len(logs) != 1 or not servers or logs[0].get(
            "proxy_duration_ms") is None:
        return None
    stamp = datetime.fromisoformat(logs[0]["start_utc"].replace("Z", "+00:00"))
    proxy_finish_us = stamp.timestamp() * 1_000_000 + \
        logs[0]["proxy_duration_ms"] * 1000
    return min(span["start_us"] for span in servers) - proxy_finish_us


def run_summary(run, old):
    old_controls = old["controls"]
    if run["run_id"] != old["run_id"]:
        raise ValueError("Old comparator belongs to another run")
    control_minutes = {}
    for name in CONTROLS:
        source = run["control_summary"][name]
        old_row = old_controls[name]
        values = source["minutes"]
        if len(values) != 25 or len(old_row["minute_ratios"]) != 15:
            raise ValueError("Not a complete 10+5+10 control window")
        for index in range(15):
            if abs(values[10 + index]["p95_ratio"] - old_row[
                    "minute_ratios"][index]) > 1e-10:
                raise ValueError("Client p95 differs from locked old aggregation")
        if sum(row["attempted"] for row in values) != (1500 if name ==
                "image_asset" else 3000):
            raise ValueError("Scheduled control requests missing")
        control_minutes[name] = values
    by_minute = defaultdict(list)
    for row in run["selected_requests"]:
        by_minute[(row["control"], row["completion_minute"])].append(row)
    details = {}
    for (name, minute), requests in sorted(by_minute.items()):
        proxy = [entry["proxy_duration_ms"] for row in requests
                 for entry in row["proxy_access"] if entry.get(
                     "proxy_duration_ms") is not None]
        completed = [row["latency_ms"] for row in requests]
        paired = [(row["latency_ms"], row["proxy_access"][0][
            "proxy_duration_ms"]) for row in requests if len(row["proxy_access"]) == 1
            and row["proxy_access"][0].get("proxy_duration_ms") is not None]
        baseline = run["control_summary"][name]["baseline_median_minute_p95_ms"]
        slow = [row for row in requests if row["latency_ms"] > 1.25 * baseline]
        pods = defaultdict(list)
        for row in requests:
            if name == "cart_view":
                uid = (row["frontend_server_spans"][0].get("pod_uid")
                       if row["frontend_server_spans"] else None)
                pods[uid or "UNATTRIBUTED"].append(row)
        pod_rows = {}
        for uid, items in pods.items():
            skew = [delta for row in items
                    if (delta := proxy_start_delta(row)) is not None]
            pod_rows[uid] = {"client_latency_ms": distribution(
                [item["latency_ms"] for item in items]),
                "proxy_duration_ms": distribution([entry["proxy_duration_ms"]
                    for item in items for entry in item["proxy_access"] if
                    entry.get("proxy_duration_ms") is not None]),
                "frontend_server_duration_ms": distribution([
                    item["frontend_server_max_duration_ms"] for item in items]),
                "raw_frontend_start_minus_proxy_finish_us": distribution(skew),
                "frontend_start_after_proxy_finish_count": sum(value > 1000
                                                                 for value in skew)}
        details[name + "/" + str(minute)] = {
            "attempted": len(requests), "successes": sum(row["success"]
                                                            for row in requests),
            "status_codes": dict(Counter(str(row["status_code"])
                                         for row in requests)),
            "client_latency_ms": distribution(completed),
            "proxy_duration_ms": distribution(proxy),
            "client_minus_proxy_ms": distribution([a - b for a, b in paired]),
            "client_proxy_pearson": pearson([a for a, _ in paired],
                                            [b for _, b in paired]),
            "target_server_duration_ms": distribution([
                row["target_server_max_duration_ms"] for row in requests]),
            "frontend_server_duration_ms": distribution([
                row["frontend_server_max_duration_ms"] for row in requests]),
            "slow_above_1_25_before_p95_count": len(slow),
            "slow_above_1_25_before_p95_trace_ids": [row["trace_id"]
                                                     for row in slow],
            "pod_breakdown": pod_rows}
        if details[name + "/" + str(minute)]["client_latency_ms"]["p95"] != \
                control_minutes[name][minute]["p95"]:
            raise ValueError("Selected raw request p95 differs from full run")
    return {"run_id": run["run_id"], "seed": run["seed"],
        "old_aggregation_exact_reproduction": True,
        "completion_vs_start_boundary_crossing": {name: run[
            "control_summary"][name]["boundary_crossing_requests"]
            for name in CONTROLS},
        "control_minutes": control_minutes,
        "targeted_minute_layer_breakdown": details,
        "conditional_block_bootstrap": bootstrap(control_minutes, run["seed"]),
        "fixed_controls_full_run_trace_joins": {"proxy": run[
            "proxy_access_matched_trace_ids"],
            "spans": run["span_matched_trace_ids"],
            "issued": run["all_control_trace_ids"]}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-analysis", type=Path, required=True)
    parser.add_argument("--old-result", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    source = read(args.control_analysis)
    old = {row["run_id"]: row for path in args.old_result
           for row in [read(path)]}
    runs = [run_summary(run, old[run["run_id"]]) for run in source["runs"]]
    result = {"status": "REQUEST_LEVEL_CONTROL_STATISTICAL_AUDIT",
        "runs": runs, "source_sha256": {str(path): sha(path) for path in
            [args.control_analysis, *args.old_result]},
        "analysis_code_sha256": sha(Path(__file__)),
        "private_gt_read": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "runs": len(runs),
        "old_aggregation_reproduced": all(row[
            "old_aggregation_exact_reproduction"] for row in runs)}))


if __name__ == "__main__":
    main()
