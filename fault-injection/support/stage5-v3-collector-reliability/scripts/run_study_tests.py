#!/usr/bin/env python3
"""Run isolated synthetic regression tests and parse all new Python sources."""

import argparse
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path("/root/stage5-v3-collector-reliability")
DEFAULT_OUTPUT = ROOT / "results/protocol-tests.json"
SUITES = {"collector_reliability": ROOT,
          "stage5_v3_draft": Path("/root/stage5-v3-cross-layer-rca"),
          "clean_gate_v2_reference": Path("/root/stage5-v3-clean-gate-v2")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    scripts = sorted((ROOT / "scripts").glob("*.py"))
    for script in scripts:
        ast.parse(script.read_text(), filename=str(script))
    designs = sorted((ROOT / "design").glob("*.yaml"))
    for design in designs:
        yaml.safe_load(design.read_text())
    results = {}
    for name, root in SUITES.items():
        env = {**os.environ, "PYTHONPATH": str(root)}
        run = subprocess.run(["python3", "-B", "-m", "unittest", "discover",
            "-s", str(root / "tests"), "-q"], capture_output=True, text=True,
            timeout=90, env=env)
        output = run.stdout + run.stderr
        count = re.search(r"Ran (\d+) tests?", output)
        results[name] = {"status": "PASS" if run.returncode == 0 and count else "FAIL",
            "test_count": int(count.group(1)) if count else 0,
            "exit_code": run.returncode, "output": output[-1400:]}
    value = {"status": "PASS" if all(row["status"] == "PASS" for row in results.values())
        else "FAIL", "at_utc": datetime.now(timezone.utc).isoformat(),
        "scripts_ast_parsed": len(scripts), "yaml_parsed": len(designs),
        "total_tests": sum(row["test_count"] for row in results.values()),
        "suites": results, "real_kubernetes_or_fault_connected": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "scripts": len(scripts),
        "yaml": len(designs), "tests": value["total_tests"]}))
    if value["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
