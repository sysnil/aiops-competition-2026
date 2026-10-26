"""Lock two DRAFT candidates using only historical Calibration evidence."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
INPUTS = (
    "design/clean-gate-v2-proposed.yaml",
    "design/trace-completion-v2-proposed.yaml",
    "design/validation-prereg-draft.yaml",
    "snapshots/historical-input-manifest.json",
    "results/historical-window-analysis.json",
    "results/historical-control-candidates.json",
    "results/cpu-reference-analysis.json",
    "results/D3-late-classification.json",
    "results/VAL-1-late-span-ids-v2.json",
    "results/VAL-2-late-span-ids-v2.json",
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    output = ROOT / "design/clean-rule-candidate-lock.json"
    if output.exists():
        raise ValueError("Candidate lock exists")
    policy = yaml.safe_load((ROOT / INPUTS[0]).read_text())
    trace = yaml.safe_load((ROOT / INPUTS[1]).read_text())
    prereg = yaml.safe_load((ROOT / INPUTS[2]).read_text())
    history = json.loads((ROOT / INPUTS[4]).read_text())
    candidate_history = json.loads((ROOT / INPUTS[5]).read_text())
    if policy["status"] != trace["status"] or policy["status"] != \
            "PROPOSED_NOT_APPROVED" or history["window_count"] != 7 or \
            len(candidate_history["runs"]) != 7 or prereg[
            "new_full_calibration_windows"] != 0:
        raise ValueError("Historical Calibration inputs or DRAFT scope differ")
    seeds = [row["seed"] for row in prereg["validation"]["runs"]]
    if seeds != [861201, 861202] or len(set(seeds)) != 2:
        raise ValueError("Unexpected independent Validation seeds")
    evidence = {str(ROOT / name): sha(ROOT / name) for name in INPUTS}
    value = {"status": "PROPOSED_NOT_APPROVED_LOCKED_BEFORE_VALIDATION",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "candidates": ["B_persistence_peak", "E_B_and_window"],
        "B": {"high_minute_ratio_gt": 1.25,
              "max_consecutive_high_minutes": 1,
              "single_minute_ratio_ceiling": 1.50,
              "any_client_error_timeout_or_missing_arrival": "FAIL"},
        "E": {"requires_B": True, "max_high_minute_fraction": .10,
              "pooled_request_p95_ratio_max": 1.25},
        "both_controls_simultaneously_high": "FAIL",
        "minimum_successful_requests_per_control_minute": 30,
        "minimum_usable_edge_spans_per_control_minute": 30,
        "cpu_safety": {"worker_min_headroom_cores": 2.0,
                       "worker_min_headroom_fraction": .25,
                       "cpu_psi_some_avg60_percent_max": 5.0,
                       "sustained_violation_abort_seconds": 60},
        "trace_completion": {"first_start": 5, "plus60_start": 60,
            "plus120_start": 120, "plus120_finish_by": 180,
            "audit_start": 240, "audit_finish_by": 300,
            "scoring_snapshot": "immutable_plus120"},
        "validation_seeds": seeds,
        "historical_calibration_source_sha256": evidence,
        "new_calibration_windows": 0,
        "formal_admission_or_clean_gate_approved": False,
        "validation_results_used_to_choose_rules": False,
        "historic_results_reclassified": False}
    with output.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": value["status"], "candidates": value[
        "candidates"], "source_count": len(evidence),
        "lock_sha256": sha(output)}))


if __name__ == "__main__":
    main()
