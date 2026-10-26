#!/usr/bin/env python3
"""Review current health after the failed timer and guarded manual rollback."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess


SAFETY_SOURCE = Path("/root/stage5-v3-clean-gate-v2/scripts/global_safety.py")


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--residue", type=Path, required=True)
    parser.add_argument("--rollback", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    snapshot, residue, rollback = read(args.snapshot), read(args.residue), read(args.rollback)
    cm = json.loads(subprocess.check_output(["kubectl", "-n", "otel-demo", "get",
        "configmap", "otel-collector", "-o", "json"], text=True, timeout=30))
    deploy = json.loads(subprocess.check_output(["kubectl", "-n", "otel-demo", "get",
        "deployment", "otel-collector", "-o", "json"], text=True, timeout=30))
    original = (rollback.get("status") == "COLLECTOR_ORIGINAL_CONFIG_RESTORED" and
        cm["data"]["relay"].find("spike_limit_percentage: 25") != -1 and
        "stage5v3-reliability" not in deploy["spec"]["template"]["metadata"].get(
            "annotations", {}) and deploy["status"].get("readyReplicas") == 1)
    components = snapshot["components"]
    components["five_host_residue"] = "PASS" if residue["status"] == "HOST_RESIDUE_PASS" else "FAIL"
    components["no_active_fault"] = components["five_host_residue"]
    components["no_mass_pod_restart"] = "PASS" if original else "FAIL"
    components["independent_watchdog"] = "FAIL"
    spec = importlib.util.spec_from_file_location("global_safety", SAFETY_SOURCE)
    safety = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(safety)
    decision = safety.assess(snapshot, read(args.policy))
    non_watchdog = {key: value for key, value in decision["checks"].items()
                    if key != "component_independent_watchdog"}
    value = {"status": "POSTROLLBACK_HEALTH_PASS_WITH_WATCHDOG_DEFECT"
             if all(non_watchdog.values()) and original
             else "POSTROLLBACK_HEALTH_FAIL",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "formal_global_safety_status": decision["status"],
        "formal_global_safety_blockers": decision["blockers"],
        "independent_watchdog_failed": True,
        "original_collector_restored": original,
        "five_host_residue": residue["status"],
        "snapshot_captured_at_utc": snapshot["captured_at_utc"],
        "component_statuses": components,
        "node_conditions": snapshot["nodes"],
        "source_sha256": {str(path): sha(path) for path in (
            args.snapshot, args.residue, args.rollback, args.policy)},
        "new_faults_allowed": False}
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
        "global_safety": decision["status"],
        "blockers": decision["blockers"]}))


if __name__ == "__main__":
    main()
