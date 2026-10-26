#!/usr/bin/env python3
"""Derive final preparation status only from archived rehearsal decisions."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


RUNS = (
    ("c1", "S5V3AP-CLEAN-C1-S1"),
    ("c2-s1", "S5V3AP-CLEAN-C2-S1"),
    ("c2-s2", "S5V3AP-CLEAN-C2-S2"),
    ("c3-s1", "S5V3AP-CLEAN-C3-S1"),
    ("c3-s2", "S5V3AP-CLEAN-C3-S2"),
)


def read(path):
    return json.loads(path.read_text()) if path.is_file() else None


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_summary(root, stem, run_id):
    decision = read(root / "results" / (stem + "-decision.json"))
    analysis = read(root / "results" / (stem + "-analysis.json"))
    result = read(root / "runs" / run_id / "result.json")
    pressure = read(root / "results" / (stem + "-resource-pressure/decision.json"))
    if result is None:
        return {"run_id": run_id, "status": "NOT_RUN"}
    controls = decision.get("normal_controls", {}) if decision else {}
    recommendation = decision.get("recommendation_server_traces_before_last_five_minutes",
                                   {}) if decision else {}
    return {"run_id": run_id,
        "status": decision["status"] if decision else "UNASSESSED",
        "seed": result["seed"], "lifecycle": result["status"],
        "fault_injected": result["fault_injected"],
        "formal_case_created": result["formal_case_created"],
        "plan_sha256": result["workload_plan_sha256"],
        "base_blockers": decision.get("base_blockers") if decision else None,
        "resource": {"status": pressure["status"],
                     "added_cores": pressure["added_cores"]} if pressure else None,
        "recommendation_minute_min_by_pod": {name: min(values)
            for name, values in recommendation.items()},
        "control_gate": controls,
        "target_edge_before_min": min(decision[
            "checkout_cart_traces_before_last_five_minutes"])
            if decision and decision.get("checkout_cart_traces_before_last_five_minutes")
            else None,
        "probe_valid": decision.get("probe_valid") if decision else None,
        "trace_source_by_phase": {phase: row["trace_source"] for phase, row in
            analysis.get("source_health", {}).items()} if analysis else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--offline-checks", type=Path, required=True)
    parser.add_argument("--synthetic-demo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite final preparation summary")
    root = args.root.resolve()
    rows = [run_summary(root, stem, run_id) for stem, run_id in RUNS]
    by_id = {row["run_id"]: row for row in rows}
    final = [by_id["S5V3AP-CLEAN-C3-S1"], by_id["S5V3AP-CLEAN-C3-S2"]]
    clean_pass = (all(row["status"] == "CLEAN_QUALIFICATION_PASS" for row in final)
                  and len({row.get("plan_sha256") for row in final}) == 1
                  and all(row.get("fault_injected") is False for row in final))
    post_proxy = ["c2-s1", "c2-s2", "c3-s1", "c3-s2"]
    proxy_counts = {}
    for stem in post_proxy:
        diagnostic = read(root / "snapshots" / (stem + "-proxy-diagnostics/manifest.json"))
        proxy_counts[stem] = diagnostic.get("proxy_503_uc_count") if diagnostic else None
    prior_proxy = {}
    for stem in ("phase1-v1", "phase1-v2", "c1", "proxy-c2b-short"):
        diagnostic = read(root / "snapshots" / (stem + "-proxy-diagnostics/manifest.json"))
        if stem == "c1" and diagnostic is None:
            diagnostic = read(root / "snapshots/c1-proxy-full-diagnostics/manifest.json")
        if stem == "proxy-c2b-short" and diagnostic is None:
            diagnostic = read(root / "snapshots/proxy-c2b-short-diagnostics/manifest.json")
        prior_proxy[stem] = diagnostic.get("proxy_503_uc_count") if diagnostic else None
    proxy_pass = (all(value == 0 for value in proxy_counts.values()) and
                  prior_proxy["proxy-c2b-short"] == 0)
    rec_control_pass = all(row.get("status") == "CLEAN_QUALIFICATION_PASS" for row in final)
    checks = read(args.offline_checks)
    demo = read(args.synthetic_demo)
    executor_pass = bool(checks and checks["status"] == "PASS" and demo and
        demo["attempts"] == 56 and demo["split_states"] == {
            "DEV": "SCORES_FROZEN", "TEST": "SCORES_FROZEN"} and
        demo["real_faults"] == demo["real_gt_reads"] == demo["formal_predictions"] == 0)
    attempts = sorted((root / "attempts").glob("S5V3AP-*/result.json")) if \
        (root / "attempts").is_dir() else []
    fault_results = [read(path) for path in attempts]
    injected = sum(bool(row.get("fault_start_utc")) for row in fault_results)
    qualification_paths = sorted((root / "results").glob("S5V3AP-*-qualification.json"))
    qualifications = [read(path) for path in qualification_paths]
    all_qualified = (len(qualifications) == 8 and
                     all(row.get("status") == "QUALIFICATION_SEED_PASS"
                         for row in qualifications))
    final_health = read(root / "snapshots/health-final.json")
    final_residue = read(root / "snapshots/five-host-final.json")
    phases = {
        "A": "PASS" if proxy_pass else "PARTIAL",
        "B": "PASS" if rec_control_pass else "PARTIAL",
        "C": "PASS" if clean_pass else "BLOCKED",
        "D": "NOT_RUN" if not attempts else "PASS" if all_qualified else "PARTIAL",
        "E": "NOT_RUN" if not attempts else "PASS" if all_qualified else "PARTIAL",
        "F": "PASS" if executor_pass else "BLOCKED",
    }
    important = [
        root / "design/clean-c3-candidate-manifest.json",
        root / "runtime/proxy-c2b-apply/apply-manifest.json",
        root / "runtime/proxy-c2b-worker2-pin/manifest.json",
        root / "runtime/proxy-rollback-extension.json",
        root / "snapshots/health-final.json",
        root / "snapshots/five-host-final.json",
        args.offline_checks, args.synthetic_demo,
    ]
    important.extend(root / "results" / (stem + "-decision.json")
                     for stem, _ in RUNS)
    important.extend(qualification_paths)
    result = {"status": "READY_FOR_MECHANISM_REHEARSAL" if clean_pass
              else "PREPARATION_BLOCKED_NO_TWO_CLEAN_PASS",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "phase_status": phases, "clean_runs": rows,
        "proxy_503_before_and_short": prior_proxy,
        "proxy_503_post_repair_full_windows": proxy_counts,
        "faults_injected": injected,
        "formal_cases_created": sum(bool(row.get("formal_case_created"))
                                    for row in fault_results),
        "real_rca_predictions": 0,
        "stage5_v3_protocol_frozen": False,
        "final_health_status": final_health.get("status") if final_health else None,
        "final_residue_status": final_residue.get("status") if final_residue else None,
        "synthetic_executor": {"status": "PASS" if executor_pass else "BLOCKED",
            "attempts": demo.get("attempts") if demo else None,
            "events": demo.get("events") if demo else None,
            "event_chain_head_sha256": demo.get("event_chain_head_sha256") if demo else None},
        "important_sha256": {str(path): sha(path) for path in important if path.is_file()},
        "recommend_stage5_v3_protocol_freeze_review": bool(
            clean_pass and injected == 8 and all_qualified and executor_pass and
            final_health and final_health.get("status") == "STAGE5_BASE_HEALTH_PASS" and
            final_residue and final_residue.get("status") == "HOST_RESIDUE_PASS"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "phases": phases,
                      "faults_injected": injected}))


if __name__ == "__main__":
    main()
