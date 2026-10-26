"""Lock the independent v2 live conversion, safety, workload and scoring code."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path("/root/stage5-v3-clean-gate-v2")
OLD = Path("/root/stage5-v3-autonomous-preparation")
NEW_SCRIPTS = (
    "run_full_clean.py", "run_attribution.py", "host_io.py",
    "capture_pipeline_v3.py", "schedule_trace_completion_v2.py",
    "schedule_trace_v2.py", "trace_identity.py", "evaluate_trace_v2.py",
    "analyze_clean_v2.py", "cpu_safety_sampler.py", "evaluate_clean_v2.py",
    "stop_calibration_attempt.py", "verify_business_routes.py",
    "postprocess_clean.py", "clean_rules.py", "control_statistics.py",
    "audit_trace_timing.py", "global_safety.py", "collect_snapshot.py",
    "assemble_safety.py", "start_clean.py", "guarded_rehearsal_entry.py",
    "clean_source_lock.py")
OLD_SCRIPTS = (
    "run_load_pair_c3.py", "fixed_control_load_c3.py",
    "capture_live_c2.py", "trace_storage_reader.py",
    "schedule_trace_completion.py", "check_ingest_lag.py",
    "probe_network.py", "audit_hosts.py", "assess_resource_pressure.py",
    "assess_clean.py", "audit_public_boundary.py")
PATHS = [
    *[ROOT / "design" / name for name in (
        "clean-gate-v2-proposed.yaml", "trace-completion-v2-proposed.yaml",
        "validation-prereg.yaml", "clean-rule-candidate-lock.json",
        "global-safety-policy-draft.json")],
    *[ROOT / "scripts" / name for name in NEW_SCRIPTS],
    *[OLD / "scripts" / name for name in OLD_SCRIPTS],
    *[OLD / "design" / name for name in (
        "load-plan-c3.yaml", "load-profiles-c3.yaml",
        "workload-state-machine-c3.yaml")],
    Path("/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py"),
    Path("/root/cross-layer-workload-generation/design/people.json"),
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    hashes = {str(path): sha(path) for path in PATHS}
    if args.verify:
        stored = json.loads(args.output.read_text())
        if stored.get("status") != "CGV2_VALIDATION_SOURCE_LOCK" or \
                stored.get("sha256") != hashes:
            raise ValueError("Clean Gate v2 source drift")
        print(json.dumps({"status": "SOURCE_LOCK_VERIFIED", "files": len(hashes)}))
        return
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    value = {"status": "CGV2_VALIDATION_SOURCE_LOCK", "at_utc":
        datetime.now(timezone.utc).isoformat(), "sha256": hashes,
        "faults_injected": 0, "stage5_v3_protocol_frozen": False,
        "validation_seeds": [861201, 861202],
        "candidate_approved": False}
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": value["status"], "files": len(hashes),
                      "manifest_sha256": sha(args.output)}))


if __name__ == "__main__":
    main()
