#!/usr/bin/env python3
"""Parse DRAFT sources and run independent synthetic regression suites."""

import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path("/root/stage5-v3-fast-ready")
OUT = ROOT / "results/offline-regression.json"
INHERITED = ROOT / "results/inherited-regression.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUT.exists() or INHERITED.exists():
        raise ValueError("Refusing overwrite")
    scripts = sorted((ROOT / "scripts").glob("*.py"))
    yaml_files = sorted((ROOT / "design").glob("*.yaml"))
    for path in scripts:
        ast.parse(path.read_text(), filename=str(path))
    for path in yaml_files:
        yaml.safe_load(path.read_text())
    local = subprocess.run(["/usr/bin/python3", "-B", "-m", "unittest",
        "discover", "-s", str(ROOT / "tests"), "-q"],
        capture_output=True, text=True, timeout=90)
    count = re.search(r"Ran (\d+) tests?", local.stdout + local.stderr)
    inherited = subprocess.run(["/usr/bin/python3", "-B",
        "/root/stage5-v3-collector-reliability/scripts/run_study_tests.py",
        "--output", str(INHERITED)], capture_output=True, text=True, timeout=150)
    old = json.loads(INHERITED.read_text()) if INHERITED.exists() else {}
    checks = {
        "fastready_synthetic_tests": local.returncode == 0 and bool(count),
        "inherited_draft_collector_and_clean_gate_tests": inherited.returncode == 0
            and old.get("status") == "PASS" and old.get("total_tests", 0) >= 83,
        "new_sources_parse": bool(scripts),
        "draft_yaml_parse": bool(yaml_files),
        "draft_invariants": json.loads((ROOT / "results/draft-invariants.json").read_text())[
            "status"] == "DRAFT_INVARIANTS_PASS",
        "watchdog_real_systemd": json.loads((ROOT / "results/watchdog.json").read_text())[
            "status"] == "ROLLBACK_WATCHDOG_READY",
        "trace_schedule": json.loads((ROOT / "results/trace-schedule.json").read_text())[
            "status"] == "TIMED_TRACE_CAPTURE_READY",
    }
    value = {"status": "OFFLINE_REGRESSION_PASS" if all(checks.values()) else
        "OFFLINE_REGRESSION_FAIL", "at_utc": datetime.now(timezone.utc).isoformat(),
        "checks": checks, "new_test_count": int(count.group(1)) if count else 0,
        "inherited_test_count": old.get("total_tests"),
        "parsed_scripts": len(scripts), "parsed_yaml": len(yaml_files),
        "new_test_output": (local.stdout + local.stderr)[-1400:],
        "inherited_test_output": (inherited.stdout + inherited.stderr)[-1400:],
        "source_sha256": {str(path): sha(path) for path in (*scripts, *yaml_files)},
        "synthetic_only": True, "fault_injected": False}
    OUT.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
        "new_tests": value["new_test_count"], "inherited_tests": value[
            "inherited_test_count"]}))
    if value["status"] != "OFFLINE_REGRESSION_PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
