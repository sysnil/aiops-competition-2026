#!/usr/bin/env python3
"""Fail-closed Stage5-v3 Fast-Ready decision from independent evidence."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path("/root/stage5-v3-fast-ready")
FILES = {name: ROOT / "results" / filename for name, filename in {
    "watchdog": "watchdog.json",
    "metrics": "collector-metrics.json",
    "trace": "trace-schedule.json",
    "smoke": "observability-smoke.json",
    "readiness": "readiness-by-layer.json",
    "clean": "clean-gate.json",
    "safety": "final-global-safety.json",
    "draft": "draft-invariants.json",
    "tests": "offline-regression.json",
}.items()}
OUT = ROOT / "results/final-decision.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUT.exists():
        raise ValueError("Refusing overwrite")
    rows = {name: json.loads(path.read_text()) for name, path in FILES.items()}
    checks = {
        "rollback_watchdog": rows["watchdog"]["status"] == "ROLLBACK_WATCHDOG_READY",
        "timed_trace": rows["trace"]["status"] == "TIMED_TRACE_CAPTURE_READY",
        "one_no_fault_smoke": rows["smoke"]["status"] == "OBSERVABILITY_SMOKE_PASS",
        "final_global_safety": rows["safety"]["status"] == "GLOBAL_SAFETY_PASS",
        "draft_invariants": rows["draft"]["status"] == "DRAFT_INVARIANTS_PASS",
        "offline_regression": rows["tests"]["status"] == "OFFLINE_REGRESSION_PASS",
        "no_fault_injected": rows["readiness"]["faults_injected"] == 0 and
            rows["clean"]["full_clean_windows_started_this_round"] == 0,
    }
    core = all(checks.values())
    formal = (core and rows["clean"]["status"] == "CLEAN_READY_2_OF_2" and
        rows["readiness"]["FORMAL_QUALIFICATION_ALLOWED"])
    value = {"status": "FAST_READY_PARTIAL_ENGINEERING_ONLY" if core else
        "FAST_READY_ENGINEERING_FAILED",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "checks": checks, "blockers": [key for key, passed in checks.items() if not passed],
        "GLOBAL_SAFETY_STATUS": rows["safety"]["status"],
        "OBSERVABILITY_STATUS": rows["smoke"]["status"],
        "TIMED_TRACE_CAPTURE_STATUS": rows["trace"]["status"],
        "ROLLBACK_WATCHDOG_STATUS": rows["watchdog"]["status"],
        "COLLECTOR_STATUS": rows["metrics"]["status"],
        "CLEAN_PROTOCOL_STATUS": rows["clean"]["status"],
        "FORMAL_QUALIFICATION_ALLOWED": bool(formal),
        "TARGET_ENVIRONMENT_ENGINEERING_READY": False,
        "readiness_by_layer": {name: item["status"] for name, item in rows[
            "readiness"]["layers"].items()},
        "secondary_blockers": ["COLLECTOR_LONG_TERM_RELIABILITY_UNRESOLVED",
            "FULL_CLEAN_TEMPORARY_STORAGE_BUDGET_INSUFFICIENT",
            "CPU_SAFETY_V2_AND_TRACE_COMPLETION_V2_REQUIRE_HUMAN_APPROVAL",
            "NODE_DURING_FAULT_SAFETY_CONTRACT_PENDING",
            "NETWORK_PATH_TWO_CONTROLS_NOT_QUALIFIED"],
        "minimum_full_clean_storage_gap_bytes": (rows["clean"][
            "existing_policy_minimum_free_bytes"] + rows["clean"][
            "minimum_expected_temporary_bytes"] - rows["clean"][
            "current_master_available_bytes"]),
        "source_sha256": {str(path): sha(path) for path in FILES.values()},
        "faults_injected": 0, "formal_cases_created": 0,
        "stage5_v2_test_accessed": False, "future_stage5_v3_gt_read": False,
        "rca_formal_predictions_run": 0, "stage5_v3_protocol_frozen": False}
    OUT.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
        "formal_allowed": value["FORMAL_QUALIFICATION_ALLOWED"],
        "layers": value["readiness_by_layer"]}))
    if not core:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
