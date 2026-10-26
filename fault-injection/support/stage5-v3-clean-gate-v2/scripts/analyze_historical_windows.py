"""Window-level exploratory control/CPU statistics from immutable summaries."""

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS = (1.10, 1.20, 1.25, 1.30, 1.50)


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def longest_run(values):
    longest = current = 0
    for value in values:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def wilson(successes, total, z=1.96):
    if total == 0:
        return None
    p = successes / total
    den = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / den
    half = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** .5) / den
    return [max(0, centre - half), min(1, centre + half)]


def config(run_id):
    if "C2-S" in run_id:
        return "C2_product_detail_control"
    if "C3-S" in run_id:
        return "C3_no_fixed_product_detail"
    if "CAL-D3" in run_id:
        return "D3_full_capture_safety_failed"
    if "VAL-" in run_id:
        return "current_full_capture"
    raise ValueError("Unexpected historical run: " + run_id)


def control_row(value):
    bins = value["minutes"]
    if len(bins) != 25 or [row["minute"] for row in bins] != list(range(25)):
        raise ValueError("Historical control is not one full 25-minute window")
    evaluated = bins[10:]
    ratios = [row["p95_ratio"] for row in evaluated]
    if any(x is None for x in ratios):
        raise ValueError("Control p95 ratio missing")
    thresholds = {str(threshold): {"minutes": sum(x > threshold for x in ratios),
        "fraction_of_15_minutes": sum(x > threshold for x in ratios) / 15,
        "longest_consecutive_minutes": longest_run(x > threshold for x in ratios)}
        for threshold in THRESHOLDS}
    return {"total_attempted": sum(row["attempted"] for row in bins),
        "total_successful": sum(row["successful"] for row in bins),
        "total_failed": sum(row["failed"] for row in bins),
        "minimum_successful_per_minute": min(row["successful"] for row in bins),
        "before_median_of_minute_p95_ms": value["before_minute_p95_median_ms"],
        "evaluated_median_of_minute_p95_ms": statistics.median(
            row["p95"] for row in evaluated),
        "evaluated_max_minute_p95_ratio": max(ratios),
        "evaluated_ratio_lag1_correlation": value.get("ratio_lag1_correlation"),
        "thresholds": thresholds, "error_types": {key: sum(row["error_types"].get(
            key, 0) for row in bins) for key in {type_name for row in bins
            for type_name in row["error_types"]}},
        "old_rule_pass_recorded": value["all_minute_old_gate_pass"]}


def main():
    references = ROOT / "snapshots/reference"
    files = [references / name for name in (
        "historical-control-statistics-v2.json",
        "S5V3AP-CAL-D3-control-statistics.json",
        "S5V3AP-VAL-1-control-statistics.json",
        "S5V3AP-VAL-2-control-statistics.json")]
    all_runs = [row for path in files for row in read(path)["runs"]]
    if len(all_runs) != 7 or len({row["run_id"] for row in all_runs}) != 7:
        raise ValueError("Expected four C2/C3, D3, and two VAL windows")
    minute_rows = []
    windows = []
    for run in all_runs:
        worker3 = run["node_cpu_median_cores_per_minute"]["k8s-worker3"]
        if len(worker3) != 25:
            raise ValueError("Node CPU window incomplete")
        controls = {name: control_row(value) for name, value in
                    run["controls"].items()}
        both_high = [minute for minute in range(10, 25) if all(
            run["controls"][name]["minutes"][minute]["p95_ratio"] > 1.25
            for name in ("image_asset", "cart_view"))]
        for name, value in run["controls"].items():
            for row in value["minutes"]:
                minute_rows.append({"run_id": run["run_id"],
                    "configuration": config(run["run_id"]), "control": name,
                    "minute": row["minute"], "phase": row["phase"],
                    "attempted": row["attempted"],
                    "successful": row["successful"], "failed": row["failed"],
                    "p50_ms": row["p50"], "p90_ms": row["p90"],
                    "p95_ms": row["p95"], "p99_ms": row["p99"],
                    "p95_ratio": row["p95_ratio"],
                    "worker3_node_cpu_cores": worker3[row["minute"]]})
        windows.append({"run_id": run["run_id"], "seed": run["seed"],
            "configuration": config(run["run_id"]),
            "old_rule_pass_recorded": run["old_rule_window_pass"],
            "controls": controls,
            "simultaneous_both_controls_above_1_25_minutes": both_high,
            "worker3_node_cpu_before_median_cores": statistics.median(worker3[:10]),
            "worker3_node_cpu_during_after_median_cores": statistics.median(
                worker3[10:]),
            "worker3_node_cpu_full_median_cores": statistics.median(worker3),
            "worker3_node_cpu_full_max_minute_cores": max(worker3),
            "worker3_node_cpu_full_max_fraction_of_8_cores": max(worker3) / 8,
            "infrastructure_valid_for_clean": "CAL-D3" not in run["run_id"]})
    current = [row for row in windows if row["configuration"] ==
               "current_full_capture"]
    rejected = sum(not row["old_rule_pass_recorded"] for row in current)
    result = {"status": "EXPLORATORY_HISTORICAL_ANALYSIS", "at_utc":
        datetime.now(timezone.utc).isoformat(), "windows": windows,
        "window_count": len(windows), "minute_rows": len(minute_rows),
        "current_configuration_old_rule_rejection": {
            "rejected_windows": rejected, "total_windows": len(current),
            "descriptive_wilson_95": wilson(rejected, len(current)),
            "confidence_warning": "Two independent windows; minutes within a window are correlated"},
        "mixed_configuration_old_rule_rejection": {
            "rejected_windows": sum(not row["old_rule_pass_recorded"]
                                    for row in windows), "total_windows": len(windows),
            "must_not_pool_as_one_identical_workload": True},
        "input_sha256": {str(path): sha(path) for path in files},
        "historic_outcomes_changed": False}
    output = ROOT / "results/historical-window-analysis.json"
    csv_path = ROOT / "results/historical-minute-controls.csv"
    if output.exists() or csv_path.exists():
        raise ValueError("Refusing overwrite")
    with output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    with csv_path.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(minute_rows[0]))
        writer.writeheader()
        writer.writerows(minute_rows)
    print(json.dumps({"status": result["status"], "windows": len(windows),
                      "minute_rows": len(minute_rows),
                      "current_old_rule_rejected": rejected}))


if __name__ == "__main__":
    main()
