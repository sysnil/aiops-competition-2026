#!/usr/bin/env python3
"""Existing Global Safety checks with verified no-fault watchdog capability."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess


SAFETY_SOURCE = Path("/root/stage5-v3-clean-gate-v2/scripts/global_safety.py")
PROBE_NAME = "s5v3-fastready-watchdog-probe-20261013"


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--residue", type=Path, required=True)
    parser.add_argument("--watchdog-noop", type=Path, required=True)
    parser.add_argument("--watchdog-restore", type=Path, required=True)
    parser.add_argument("--collector-noop", type=Path, required=True)
    parser.add_argument("--cleanup", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    snapshot, residue = read(args.snapshot), read(args.residue)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(
        residue["at_utc"])).total_seconds()
    if not 0 <= age <= 180:
        raise ValueError("Five-host residue is stale")
    noop, restore, collector, cleanup = [read(path) for path in (
        args.watchdog_noop, args.watchdog_restore, args.collector_noop,
        args.cleanup)]
    watchdog = (noop["status"] == restore["status"] ==
        "SYSTEMD_WATCHDOG_ROLLBACK_PASS" and
        noop["action"] == "NOOP_BASELINE_ALREADY_PRESENT" and
        restore["action"] == "RESTORED_CANDIDATE" and
        all(item["business_pod_uids_unchanged"] for item in (noop, restore, cleanup)) and
        cleanup["status"] == "ISOLATED_TEST_OBJECT_REMOVED" and
        collector["status"] == "COLLECTOR_ORIGINAL_CONFIG_RESTORED" and
        collector["state"]["configuration"] == "ORIGINAL")
    command = ["/usr/bin/kubectl", "--kubeconfig=/etc/kubernetes/admin.conf",
        "--context=kubernetes-admin@kubernetes", "-n", "otel-demo"]
    deployments = json.loads(subprocess.check_output([*command, "get", "deployments",
        "-o", "json"], text=True, timeout=40))["items"]
    deployments_ready = all(dep["status"].get("readyReplicas", 0) == dep["spec"].get(
        "replicas", 1) for dep in deployments)
    probe = subprocess.check_output([*command, "get", "configmap", PROBE_NAME,
        "--ignore-not-found", "-o", "name"], text=True, timeout=30).strip()
    snapshot["components"].update({
        "five_host_residue": "PASS" if residue["status"] == "HOST_RESIDUE_PASS" else "FAIL",
        "independent_watchdog": "PASS" if watchdog and not probe else "FAIL",
        "no_active_fault": "PASS" if residue["status"] == "HOST_RESIDUE_PASS" else "FAIL",
        "no_mass_pod_restart": "PASS" if deployments_ready else "FAIL"})
    spec = importlib.util.spec_from_file_location("global_safety", SAFETY_SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    decision = module.assess(snapshot, read(args.policy))
    decision.update({"at_utc": datetime.now(timezone.utc).isoformat(),
        "watchdog_capability_only_no_active_fault_timer": True,
        "canary_allowed": decision["status"] == "GLOBAL_SAFETY_PASS",
        "faults_allowed": False,
        "formal_fault_qualification_authorized": False,
        "five_host_residue": residue["status"],
        "all_deployments_ready": deployments_ready,
        "test_configmap_removed": not bool(probe),
        "source_sha256": {str(path): sha(path) for path in (
            args.snapshot, args.residue, args.watchdog_noop,
            args.watchdog_restore, args.collector_noop, args.cleanup,
            args.policy)}})
    args.output.write_text(json.dumps(decision, indent=2) + "\n")
    print(json.dumps({"status": decision["status"],
        "blockers": decision["blockers"], "canary_allowed": decision["canary_allowed"]}))


if __name__ == "__main__":
    main()
