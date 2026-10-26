"""Analyze immutable no-fault client records without changing historical gates."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics


CONTROLS = ("image_asset", "cart_view")
THRESHOLDS = (1.10, 1.20, 1.25, 1.30, 1.50)


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)]


def minute_rows(path, start):
    bins = defaultdict(list)
    with path.open() as stream:
        for line in stream:
            row = json.loads(line)
            stamp = row.get("time_utc", row.get("scheduled_utc"))
            minute = int((datetime.fromisoformat(stamp).timestamp() - start) // 60)
            if 0 <= minute < 25:
                bins[minute].append(row)
    return bins


def longest_run(flags):
    best = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        best = max(best, current)
    return best


def pearson(a, b):
    rows = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if len(rows) < 3:
        return None
    xs, ys = zip(*rows)
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    if sx == 0 or sy == 0:
        return None
    return sum((x - statistics.mean(xs)) * (y - statistics.mean(ys))
               for x, y in rows) / (len(rows) * sx * sy)


def analyze(run):
    schedule_path = run / "load_pair/schedule.json"
    schedule = json.loads(schedule_path.read_text())
    start = datetime.fromisoformat(schedule["scheduled_start_utc"]).timestamp()
    result = {"run_id": schedule["run_id"], "seed": schedule["seed"],
              "source_sha256": {str(schedule_path): hashlib.sha256(
                  schedule_path.read_bytes()).hexdigest()}, "controls": {}}
    node_bins = defaultdict(lambda: defaultdict(list))
    for phase in ("before", "during", "after"):
        path = run / "capture-pipeline/captures" / phase / "metrics/node_cpu.json"
        result["source_sha256"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        for series in json.loads(path.read_text())["data"]["result"]:
            node = series["metric"].get("k8s_node_name")
            for stamp, value in series["values"]:
                m = int((float(stamp) - start) // 60)
                if node and 0 <= m < 25:
                    node_bins[node][m].append(float(value))
    node_minutes = {node: [statistics.median(by_minute[m]) if by_minute[m]
                           else None for m in range(25)]
                    for node, by_minute in node_bins.items()}
    result["node_cpu_median_cores_per_minute"] = node_minutes
    flags = {}
    for control in CONTROLS:
        path = run / "load_pair/controls" / (control + ".jsonl")
        result["source_sha256"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        bins = minute_rows(path, start)
        before_p95 = [percentile([r["latency_ms"] for r in bins[m]
                                  if r.get("success") and isinstance(r.get("latency_ms"),
                                                                     (int, float))], .95)
                      for m in range(10)]
        if any(x is None for x in before_p95):
            raise ValueError("Before has a missing client p95 minute")
        baseline = percentile(before_p95, .50)
        minutes = []
        for m in range(25):
            rows = bins[m]
            successes = [r["latency_ms"] for r in rows if r.get("success") and
                         isinstance(r.get("latency_ms"), (int, float))]
            values = {"p" + str(int(p * 100)): percentile(successes, p)
                      for p in (.50, .90, .95, .99)}
            attempted = len(rows)
            failed = sum(not r.get("success", False) for r in rows)
            minutes.append({"minute": m, "phase": "before" if m < 10 else
                            "during" if m < 15 else "after", "attempted": attempted,
                            "successful": len(successes), "failed": failed,
                            "error_rate": failed / attempted if attempted else None,
                            "error_types": dict(Counter(
                                r.get("error_type") or r.get("status") or "UNKNOWN"
                                for r in rows if not r.get("success", False))),
                            **values, "p95_ratio": values["p95"] / baseline
                            if values["p95"] is not None and baseline > 0 else None})
        evaluated = minutes[10:]
        ratios = [r["p95_ratio"] for r in evaluated]
        above = {str(t): [r is not None and r > t for r in ratios]
                 for t in THRESHOLDS}
        flags[control] = above["1.25"]
        result["controls"][control] = {
            "before_minute_p95_median_ms": baseline, "minutes": minutes,
            "evaluated_minute_count": len(evaluated),
            "threshold_exceedance": {key: {"minutes": sum(value),
                "fraction": sum(value) / len(value), "longest_consecutive":
                longest_run(value)} for key, value in above.items()},
            "ratio_distribution": {"min": min(x for x in ratios if x is not None),
                "median": percentile([x for x in ratios if x is not None], .50),
                "p90": percentile([x for x in ratios if x is not None], .90),
                "p95": percentile([x for x in ratios if x is not None], .95),
                "max": max(x for x in ratios if x is not None)},
            "request_count_vs_ratio_correlation": pearson(
                [r["successful"] for r in evaluated], ratios),
            "ratio_lag1_correlation": pearson(ratios[1:], ratios[:-1]),
            "worker2_cpu_vs_ratio_correlation": pearson(
                node_minutes.get("k8s-worker2", [None] * 25)[10:], ratios),
            "worker3_cpu_vs_ratio_correlation": pearson(
                node_minutes.get("k8s-worker3", [None] * 25)[10:], ratios),
            "client_failures_evaluated": sum(r["failed"] for r in evaluated),
            "all_minute_old_gate_pass": all(x is not None and x <= 1.25
                                             for x in ratios),
        }
    result["simultaneous_above_1_25_minutes"] = sum(
        a and b for a, b in zip(flags[CONTROLS[0]], flags[CONTROLS[1]]))
    result["old_rule_window_pass"] = all(
        row["all_minute_old_gate_pass"] for row in result["controls"].values())
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    runs = [analyze(path) for path in args.run]
    old_rejections = sum(not row["old_rule_window_pass"] for row in runs)
    result = {"status": "DESCRIPTIVE_NO_FAULT_EXPLORATORY", "runs": runs,
              "window_count": len(runs), "old_rule_rejected_windows": old_rejections,
              "old_rule_rejection_fraction": old_rejections / len(runs),
              "independent_unit": "complete_25_minute_run_not_minute_bucket",
              "uncertainty": "Four historical windows are too few to estimate a stable false-rejection rate",
              "historical_admission_reclassification": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"runs": len(runs), "old_rejections": old_rejections}))


if __name__ == "__main__":
    main()
