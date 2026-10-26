#!/usr/bin/env python3
"""Run new disk guards plus inherited DRAFT synthetic regression suites."""

import ast
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path("/root/stage5-v3-fast-ready")
OUTPUT = ROOT / "results/data-disk-offline-regression.json"
INHERITED = ROOT / "results/inherited-regression-data-disk.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--inherited-output", type=Path, default=INHERITED)
    args = parser.parse_args()
    if args.output.exists() or args.inherited_output.exists():
        raise ValueError("Refusing overwrite")
    scripts = sorted((ROOT / "scripts").glob("*.py"))
    designs = sorted((ROOT / "design").glob("*.yaml"))
    for path in scripts:
        ast.parse(path.read_text(), filename=str(path))
    for path in designs:
        yaml.safe_load(path.read_text())
    local = subprocess.run(["/usr/bin/python3", "-B", "-m", "unittest",
        "discover", "-s", str(ROOT / "tests"), "-q"], capture_output=True,
        text=True, timeout=90)
    count = re.search(r"Ran (\d+) tests?", local.stdout + local.stderr)
    old = subprocess.run(["/usr/bin/python3", "-B",
        "/root/stage5-v3-collector-reliability/scripts/run_study_tests.py",
        "--output", str(args.inherited_output)], capture_output=True, text=True, timeout=150)
    inherited = json.loads(args.inherited_output.read_text()) if args.inherited_output.exists() else {}
    decision = json.loads((ROOT / "results/data-disk-validation.json").read_text())
    paths = json.loads((ROOT / "results/output-path-audit.json").read_text())
    checks = {"new_unit_tests": local.returncode == 0 and count is not None and
        int(count.group(1)) >= 18,
        "inherited_synthetic_tests": old.returncode == 0 and inherited.get(
            "status") == "PASS" and inherited.get("total_tests", 0) >= 83,
        "new_sources_parse": len(scripts) >= 25,
        "draft_yaml_parse": len(designs) >= 4,
        "actual_disk_archive_and_restore": decision["status"] ==
            "STAGE5V3_DATA_DISK_VALIDATED",
        "output_path_guard_audit": paths["status"] ==
            "STAGE5V3_DRAFT_RAW_OUTPUT_PATHS_AUDITED"}
    value = {"status": "DATA_DISK_REGRESSION_PASS" if all(checks.values()) else
        "DATA_DISK_REGRESSION_FAIL", "at_utc": datetime.now(timezone.utc).isoformat(),
        "checks": checks, "new_test_count": int(count.group(1)) if count else 0,
        "inherited_test_count": inherited.get("total_tests"),
        "parsed_python_files": len(scripts), "parsed_yaml_files": len(designs),
        "test_output_tail": (local.stdout + local.stderr)[-1400:],
        "inherited_output_tail": (old.stdout + old.stderr)[-1400:],
        "source_sha256": {str(path): sha(path) for path in (*scripts, *designs)},
        "fault_injected": False}
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
        "new_tests": value["new_test_count"],
        "inherited_tests": value["inherited_test_count"]}))
    if value["status"] != "DATA_DISK_REGRESSION_PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
