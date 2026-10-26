#!/usr/bin/env python3
"""Pin one no-fault C3 rehearsal candidate, not the Stage5-v3 protocol."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path("/root/stage5-v3-autonomous-preparation")
PLAN = ROOT / "design/load-plan-c3.yaml"
OUTPUT = ROOT / "design/clean-c3-candidate-manifest.json"
FILES = [
    "design/load-plan-c3.yaml", "design/load-profiles-c3.yaml",
    "design/workload-state-machine-c3.yaml",
    "scripts/run_clean_c3.py", "scripts/run_load_pair_c3.py",
    "scripts/fixed_control_load_c3.py", "scripts/capture_pipeline_c2.py",
    "scripts/capture_live_c2.py", "scripts/trace_storage_reader.py",
    "scripts/schedule_trace_completion.py", "scripts/check_ingest_lag.py",
    "scripts/probe_network.py", "scripts/verify_business_routes.py",
    "scripts/analyze_clean_c3.py", "scripts/assess_clean.py",
    "scripts/assess_resource_pressure.py", "scripts/audit_hosts.py",
    "scripts/freeze_clean_candidate.py",
    "scripts/host_io.py", "scripts/stop_attempt_processes.py",
    "snapshots/proxy-upstream-repair-c2b-preparation/frontend-proxy-envoy-c2.tmpl.yaml",
    "runtime/proxy-c2b-worker2-pin/deployment-after-pin.json",
]
EXTERNAL = [
    Path("/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py"),
    Path("/root/cross-layer-workload-generation/design/people.json"),
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_plan():
    value = yaml.safe_load(PLAN.read_text())
    profile = yaml.safe_load((ROOT / 'design/load-profiles-c3.yaml').read_text())[
        'profiles']['MEDIUM']
    controls = {row["name"] for row in value["workload"]["control_streams"]}
    if (value["revision"] != "autonomous_preparation_clean_c3" or
            value["clean_seeds"] != [851221, 851222] or
            profile['arrival_rate_rps'] != 1.4 or
            profile['concurrency'] != 4 or
            value['workload']['workflow_weights'] !=
            {'browse': 20, 'shopping': 30, 'checkout': 50} or
            controls != {"image_asset", "cart_view"} or
            value["controls"]["maximum_client_p95_ratio"] != 1.25 or
            value["workload"]["max_added_node_cpu_cores"] != 0.25 or
            [value["workload"][name] for name in
             ("before_seconds", "during_seconds", "after_seconds")] != [600, 300, 600] or
            value["capture"]["phase_start_delay_seconds"] != 5 or
            value["capture"]["trace_completion_delay_seconds"] != 60):
        raise ValueError("C3 candidate differs from the preregistered quality contract")
    return value


def verify():
    value = json.loads(OUTPUT.read_text())
    if (value.get("status") != "CLEAN_REHEARSAL_CANDIDATE_PINNED" or
            value.get("stage5_v3_protocol_frozen") is not False or
            value.get("faults_injected") != 0):
        raise ValueError("Not an independent unfaulted candidate manifest")
    validate_plan()
    for filename, expected in value["sha256"].items():
        if sha(Path(filename)) != expected:
            raise ValueError("Clean candidate source changed: " + filename)
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        result = verify()
        print(json.dumps({"status": result["status"],
                          "manifest_sha256": sha(OUTPUT)}))
        return
    if OUTPUT.exists():
        raise ValueError("Refusing overwrite of clean candidate manifest")
    validate_plan()
    sources = [*(ROOT / relative for relative in FILES), *EXTERNAL]
    if not all(path.is_file() for path in sources):
        raise ValueError("Clean candidate source or proxy provenance is absent")
    result = {"status": "CLEAN_REHEARSAL_CANDIDATE_PINNED",
              "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "clean_seeds": [851221, 851222], "faults_injected": 0,
              "formal_cases_created": 0, "stage5_v3_protocol_frozen": False,
              "sha256": {str(path): sha(path) for path in sources}}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    verify()
    print(json.dumps({"status": result["status"],
                      "manifest_sha256": sha(OUTPUT),
                      "sources": len(sources)}))


if __name__ == "__main__":
    main()
