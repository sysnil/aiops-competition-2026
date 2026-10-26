#!/usr/bin/env python3
"""Archive compile and synthetic-test results with source hashes."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Offline check archive must be new")
    root = args.root.resolve()
    checks = [
        [sys.executable, "-m", "compileall", "-q", str(root / "scripts"),
         str(root / "tests")],
        [sys.executable, "-m", "unittest", "discover", "-s", str(root / "tests"),
         "-v"],
    ]
    results = []
    for command in checks:
        process = subprocess.run(command, capture_output=True, text=True,
                                 timeout=120)
        results.append({"command": command, "exit_code": process.returncode,
                        "stdout": process.stdout, "stderr": process.stderr})
    files = sorted([*root.glob("scripts/*.py"), *root.glob("tests/*.py"),
                    *root.glob("design/*.yaml"), *root.glob("design/*.json")])
    summary = {"status": "PASS" if all(x["exit_code"] == 0 for x in results)
               else "FAIL", "at_utc": datetime.now(timezone.utc).isoformat(),
               "checks": results, "sha256": {str(path.relative_to(root)):
                   hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
               "synthetic_only": True, "faults_injected": 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(summary, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": summary["status"],
                      "tests": results[-1]["stderr"].splitlines()[-3:]}))
    if summary["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
