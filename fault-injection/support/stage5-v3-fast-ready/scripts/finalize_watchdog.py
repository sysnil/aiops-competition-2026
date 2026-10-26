#!/usr/bin/env python3
"""Summarize real systemd probe/no-op/restore and fail-closed evidence."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path("/root/stage5-v3-fast-ready")
RESULTS = ROOT / "results"
ARCHIVE = ROOT / "archive"
UNITS = {
    "readonly": "s5v3fr-kube-readonly-20261013.service",
    "noop": "s5v3fr-watchdog-noop-20261013.service",
    "real_restore": "s5v3fr-watchdog-restore-20261013.service",
    "collector_noop": "s5v3fr-collector-script-noop-20261013.service",
    "invalid_registration": "s5v3fr-watchdog-invalid-registration-20261013.service",
}


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    target = RESULTS / "watchdog.json"
    if target.exists():
        raise ValueError("Refusing overwrite")
    files = {name: RESULTS / filename for name, filename in {
        "readonly": "watchdog-systemd-readonly.json",
        "noop": "watchdog-noop.json",
        "mutation": "watchdog-test-mutation.json",
        "real_restore": "watchdog-real-restore.json",
        "collector_noop": "collector-rollback-script-noop.json",
        "cleanup": "watchdog-test-cleanup.json",
        "invalid_registration": "watchdog-failclosed-test.json"}.items()}
    data = {name: read(path) for name, path in files.items()}
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    logs = {}
    for name, unit in UNITS.items():
        path = ARCHIVE / ("watchdog-" + name + "-journal.log")
        if path.exists():
            raise ValueError("Refusing journal overwrite")
        result = subprocess.run(["journalctl", "-u", unit, "--no-pager",
            "-o", "short-iso"], capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError("Cannot read journal: " + unit)
        path.write_text(result.stdout)
        logs[name] = {"path": str(path), "sha256": sha(path),
                      "success_exit": "Deactivated successfully" in result.stdout,
                      "failure_exit": "Failed with result 'exit-code'" in result.stdout}
    plan = read(Path("/root/stage5-v3-collector-reliability/design/collector-change-plan-v2.json"))
    cm = json.loads(subprocess.check_output(["/usr/bin/kubectl",
        "--kubeconfig=/etc/kubernetes/admin.conf", "-n", "otel-demo",
        "get", "configmap", "otel-collector", "-o", "json"], text=True, timeout=20))
    deployment = json.loads(subprocess.check_output(["/usr/bin/kubectl",
        "--kubeconfig=/etc/kubernetes/admin.conf", "-n", "otel-demo",
        "get", "deployment", "otel-collector", "-o", "json"], text=True, timeout=20))
    probe = subprocess.check_output(["/usr/bin/kubectl",
        "--kubeconfig=/etc/kubernetes/admin.conf", "-n", "otel-demo",
        "get", "configmap", "s5v3-fastready-watchdog-probe-20261013",
        "--ignore-not-found", "-o", "name"], text=True, timeout=20).strip()
    original = (hashlib.sha256(cm["data"]["relay"].encode()).hexdigest() == plan[
        "original_relay_sha256"] and "stage5v3-reliability" not in deployment[
        "spec"]["template"]["metadata"].get("annotations", {}) and
        cm["metadata"]["uid"] == plan["original_configmap_uid"] and
        deployment["metadata"]["uid"] == plan["original_deployment_uid"])
    checks = {
        "systemd_readonly_correct_cluster": data["readonly"]["status"] ==
            "SYSTEMD_KUBERNETES_READONLY_PASS" and data["readonly"]["cluster"][
                "server"] == "https://192.168.53.52:6443",
        "timer_noop": data["noop"]["status"] == "SYSTEMD_WATCHDOG_ROLLBACK_PASS" and
            data["noop"]["action"] == "NOOP_BASELINE_ALREADY_PRESENT" and logs[
                "noop"]["success_exit"],
        "timer_real_isolated_restore": data["real_restore"]["status"] ==
            "SYSTEMD_WATCHDOG_ROLLBACK_PASS" and data["real_restore"]["action"] ==
            "RESTORED_CANDIDATE" and logs["real_restore"]["success_exit"],
        "collector_actual_script_systemd_noop": data["collector_noop"]["status"] ==
            "COLLECTOR_ORIGINAL_CONFIG_RESTORED" and logs["collector_noop"][
                "success_exit"],
        "invalid_registration_fails_closed": data["invalid_registration"]["status"] ==
            "WATCHDOG_INVALID_REGISTRATION_FAIL_CLOSED_PASS" and logs[
                "invalid_registration"]["failure_exit"],
        "test_object_removed_business_uids_unchanged": data["cleanup"]["status"] ==
            "ISOLATED_TEST_OBJECT_REMOVED" and data["cleanup"][
                "business_pod_uids_unchanged"] and not probe,
        "collector_original_unchanged": original,
    }
    value = {"status": "ROLLBACK_WATCHDOG_READY" if all(checks.values()) else
        "ROLLBACK_WATCHDOG_BLOCKED",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "checks": checks, "blockers": [key for key, ok in checks.items() if not ok],
        "systemd_journals": logs,
        "cluster_api": data["readonly"]["cluster"],
        "existing_kubeconfig_rbac_scope": "cluster-admin; root-owned 0600, script object allowlist",
        "no_new_credential_created": True,
        "original_collector_relay_sha256": plan["original_relay_sha256"],
        "collector_configmap_resource_version": cm["metadata"]["resourceVersion"],
        "collector_deployment_resource_version": deployment["metadata"]["resourceVersion"],
        "source_sha256": {str(path): sha(path) for path in files.values()},
        "fault_injected": False}
    target.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "blockers": value["blockers"]}))
    if value["status"] != "ROLLBACK_WATCHDOG_READY":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
