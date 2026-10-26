#!/usr/bin/env python3
"""Lock the single Collector candidate and all no-fault study sources before change."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path("/root/stage5-v3-collector-reliability")
DEFAULT_OUTPUT = ROOT / "design/source-lock.json"
LOCAL = [
    "design/collector-change-prereg.yaml", "design/collector-canary-prereg.yaml",
    "design/continuous-window-prereg.yaml", "design/network-control-prereg.yaml",
    "design/collector-change-plan-v2.json",
    "scripts/collector_change.py", "scripts/run_short_canary.py",
    "scripts/audit_short_canary.py", "scripts/audit_late_short.py",
    "scripts/run_continuous.py", "scripts/cleanup_continuous.py",
    "scripts/capture_continuous_traces.py", "scripts/compare_continuous_traces.py",
    "scripts/archive_continuous.py", "scripts/capture_packet_proof.py",
    "scripts/snapshot_topology.py", "scripts/audit_route_scope.py",
    "scripts/qualify_network_controls.py", "scripts/protocol_checks.py",
    "scripts/lock_study_sources.py", "scripts/run_study_tests.py",
    "scripts/record_metric_interface.py",
    "snapshots/prechange-20261012/manifest.json",
    "results/collector-resource-analysis.json", "results/refusal-timeline.json",
    "results/protocol-tests-v3.json", "results/precanary-metric-interface.json",
]
REFERENCES = [
    Path("/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py"),
    Path("/root/stage5-v3-autonomous-preparation/scripts/fixed_control_load_c3.py"),
    Path("/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml"),
    Path("/root/stage5-v3-autonomous-preparation/design/load-profiles-c3.yaml"),
    Path("/root/cross-layer-workload-generation/design/people.json"),
    Path("/root/stage5-v3-cross-layer-rca/DESIGN.md"),
    Path("/root/stage5-v3-clean-gate-v2/design/trace-completion-v2-proposed.yaml"),
    Path("/root/stage5-v3-autonomous-preparation/scripts/network_fault.py"),
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def namespace(seed, offsets):
    return {seed, *(seed + offset for offset in offsets)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Source lock already exists")
    candidate = json.loads((ROOT / "design/collector-change-plan-v2.json").read_text())
    tests = json.loads((ROOT / "results/protocol-tests-v3.json").read_text())
    canary = yaml.safe_load((ROOT / "design/collector-canary-prereg.yaml").read_text())
    continuous = yaml.safe_load((ROOT / "design/continuous-window-prereg.yaml").read_text())
    offsets = [item["seed_offset"] for item in canary["workload"]["control_streams"]]
    previous = [(item["id"], namespace(item["seed"], offsets)) for item in
                canary["previous_runs"]]
    planned = [(item["id"], namespace(item["seed"], offsets)) for item in
               canary["canaries"]]
    planned.append((continuous["id"], namespace(continuous["seed"], offsets)))
    collisions = [(a, b) for index, (a, left) in enumerate(planned)
        for b, right in planned[index + 1:] + previous if left & right]
    historical_collisions = [(a, b) for index, (a, left) in enumerate(previous)
        for b, right in previous[index + 1:] if left & right]
    if (candidate["candidate_semantic_diff"] != {
            "processors.memory_limiter.spike_limit_percentage": [25, 15]} or
            candidate["prereg_sha256"] != sha(ROOT / "design/collector-change-prereg.yaml") or
            canary["auditor_sha256"] != sha(ROOT / "scripts/audit_short_canary.py") or
            tests["status"] != "PASS" or collisions or
            continuous["workload"]["duration_seconds"] != 2700):
        raise ValueError("Candidate, synthetic tests or fixed workload invalid")
    paths = [ROOT / name for name in LOCAL] + REFERENCES
    if any(not path.is_file() for path in paths):
        raise ValueError("Locked source missing")
    value = {"status": "COLLECTOR_RELIABILITY_SOURCES_LOCKED",
        "locked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": {str(path): sha(path) for path in paths},
        "candidate_plan_sha256": sha(ROOT / "design/collector-change-plan-v2.json"),
        "synthetic_test_count": tests["total_tests"],
        "canary_and_continuous_seed_namespaces_disjoint": True,
        "preserved_historical_seed_collisions": historical_collisions,
        "faults_injected": 0, "formal_cases_created": 0,
        "stage5_v3_protocol_frozen": False, "private_gt_read": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "files": len(paths),
                      "sha256": sha(args.output)}))


if __name__ == "__main__":
    main()
