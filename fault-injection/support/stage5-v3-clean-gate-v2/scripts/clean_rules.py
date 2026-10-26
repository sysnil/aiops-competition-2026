"""Compare candidate no-fault control rules without approving any protocol change."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path

from control_statistics import CONTROLS, minute_rows, percentile, longest_run


def evaluate(run, envelope=None):
    schedule = json.loads((run / "load_pair/schedule.json").read_text())
    start = datetime.fromisoformat(schedule["scheduled_start_utc"]).timestamp()
    analysis_path = run.parent.parent / "results" / (run.name + "-analysis.json")
    analysis = json.loads(analysis_path.read_text()) if analysis_path.is_file() else None
    edge_names = {"image_asset": "image_asset/frontend-proxy->image-provider",
                  "cart_view": "cart_view/frontend->cart"}
    controls = {}
    for name in CONTROLS:
        bins = minute_rows(run / "load_pair/controls" / (name + ".jsonl"), start)
        p95 = []
        for minute in range(25):
            successful = [r["latency_ms"] for r in bins[minute] if
                          r.get("success") and isinstance(r.get("latency_ms"),
                                                           (int, float))]
            p95.append(percentile(successful, .95))
        baseline = percentile(p95[:10], .50) if all(x is not None for x in
                                                 p95[:10]) else None
        evaluated = [r for m in range(10, 25) for r in bins[m]]
        successful_all = [r["latency_ms"] for r in evaluated if r.get("success") and
                          isinstance(r.get("latency_ms"), (int, float))]
        missing = any(len([r for r in bins[m] if r.get("success")]) < 30
                      for m in range(25)) or baseline is None or baseline <= 0
        error = any(not r.get("success") for m in range(25) for r in bins[m])
        edge = analysis.get("fixed_control_edge_trace_ids_per_minute", {}).get(
            edge_names[name]) if analysis else None
        trace_quality = bool(edge and len(edge) == 25 and all(x >= 30 for x in edge))
        ratios = [x / baseline if x is not None and baseline else None for x in p95[10:]]
        high = [x is not None and x > 1.25 for x in ratios]
        base = (not missing and not error and trace_quality and
                all(x is not None for x in ratios))
        full_ratio = (percentile(successful_all, .95) / baseline
                      if successful_all and baseline else None)
        max_ratio = max((x for x in ratios if x is not None), default=None)
        rules = {
            "A_old": base and all(x <= 1.25 for x in ratios),
            "B_singleton_and_peak": base and longest_run(high) <= 1 and
                max_ratio is not None and max_ratio <= 1.50,
            "C_window_composite": base and full_ratio is not None and
                full_ratio <= 1.25 and sum(high) / len(high) <= .10,
        }
        rules["E_hybrid_B_and_C"] = (rules["B_singleton_and_peak"] and
                                     rules["C_window_composite"])
        if envelope is not None:
            bound = envelope[name]
            rules["D_calibrated_envelope"] = (
                base and max_ratio is not None and max_ratio <= bound and
                longest_run([x > 1.25 for x in ratios]) <= 1)
        controls[name] = {"rules": rules, "baseline_p95_ms": baseline,
                          "window_p95_ratio": full_ratio, "max_ratio": max_ratio,
                          "above_1_25": sum(high), "longest_above_1_25":
                          longest_run(high), "missing_minute": missing,
                          "client_error_or_timeout": error,
                          "trace_quality_30_each_minute": trace_quality,
                          "analysis_sha256": hashlib.sha256(analysis_path.read_bytes()
                              ).hexdigest() if analysis else None,
                          "minute_ratios": ratios}
    keys = controls[CONTROLS[0]]["rules"]
    return {"run_id": schedule["run_id"], "seed": schedule["seed"],
            "controls": controls, "combined_rules": {key: all(controls[name][
                "rules"][key] for name in CONTROLS) for key in keys}}


def calibration_envelope(run):
    result = evaluate(run)
    return {name: max(1.25, percentile(row["minute_ratios"], .95) * 1.05)
            for name, row in result["controls"].items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--rule-lock", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    envelope = None
    if args.rule_lock:
        lock = json.loads(args.rule_lock.read_text())
        if lock["status"] != "PROPOSED_NOT_APPROVED_LOCKED_BEFORE_VALIDATION":
            raise ValueError("Rule lock invalid")
        envelope = lock["calibration_envelope"]
    result = evaluate(args.run, envelope)
    result["status"] = "CANDIDATE_ONLY_NO_ADMISSION_CHANGE"
    result["rule_lock_sha256"] = (hashlib.sha256(args.rule_lock.read_bytes()).hexdigest()
                                  if args.rule_lock else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"run_id": result["run_id"], "combined_rules":
                      result["combined_rules"]}))


if __name__ == "__main__":
    main()
