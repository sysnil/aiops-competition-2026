#!/usr/bin/env python3
"""Confirm a rejected registration exits nonzero under real systemd."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


ROOT = Path("/root/stage5-v3-fast-ready")
OUTPUT = ROOT / "results/watchdog-failclosed-test.json"
REJECTED = ROOT / "results/watchdog-rejected-invalid-registration.json"


def main():
    if OUTPUT.exists() or REJECTED.exists():
        raise ValueError("Refusing overwrite")
    command = ["systemd-run", "--unit=s5v3fr-watchdog-invalid-registration-20261013",
        "--wait", "--collect", "--pipe", "--property=User=root",
        "--property=WorkingDirectory=/",
        "--property=Environment=KUBECONFIG=/etc/kubernetes/admin.conf",
        "--property=RuntimeMaxSec=120", "/usr/bin/python3", "-B",
        str(ROOT / "scripts/watchdog_probe.py"), "rollback",
        "--registration", str(ROOT / "design/watchdog-registration.json"),
        "--registration-sha256", "0" * 64, "--output", str(REJECTED)]
    run = subprocess.run(command, capture_output=True, text=True, timeout=35)
    cm = subprocess.check_output(["/usr/bin/kubectl",
        "--kubeconfig=/etc/kubernetes/admin.conf", "-n", "otel-demo",
        "get", "configmap", "s5v3-fastready-watchdog-probe-20261013",
        "--ignore-not-found", "-o", "name"], text=True, timeout=20).strip()
    value = {"status": "WATCHDOG_INVALID_REGISTRATION_FAIL_CLOSED_PASS"
        if run.returncode != 0 and not REJECTED.exists() and not cm else
        "WATCHDOG_INVALID_REGISTRATION_FAIL_CLOSED_FAIL",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "systemd_run_exit_code": run.returncode,
        "systemd_stdout_tail": run.stdout[-800:],
        "systemd_stderr_tail": run.stderr[-800:],
        "rejected_output_absent": not REJECTED.exists(),
        "isolated_test_object_absent": not bool(cm),
        "fault_injected": False}
    OUTPUT.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
                      "exit_code": run.returncode}))
    if value["status"].endswith("FAIL"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
