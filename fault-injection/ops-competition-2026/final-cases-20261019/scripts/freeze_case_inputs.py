#!/usr/bin/env python3
"""Freeze one new case's code and design without starting an attempt."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


OPS = Path("/root/ops-competition-2026")
BATCH = OPS / "final-cases-20261019"
CASE_DIRS = {
    "SF-013": (OPS / "sf013-adapter-20261019", OPS / "sf013-formal-20261019"),
    "SF-013-ATTEMPT-02": (OPS / "sf013-adapter-v2-20261019",
                          OPS / "sf013-formal-v2-20261019"),
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=CASE_DIRS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("Freeze manifest already exists")
    files = []
    for directory in CASE_DIRS[args.case]:
        files.extend(directory.glob("scripts/*.py"))
        files.extend(directory.glob("design/*.json"))
        files.extend(directory.glob("design/*.yaml"))
    suffix = "-v2" if args.case.endswith("ATTEMPT-02") else ""
    authority = ("SF-013-plan-authority-att02.json" if suffix else
                 "SF-013-plan-authority.json")
    files.extend((BATCH / f"sf013-storage{suffix}/case-budget.json",
                  BATCH / f"sf013-storage{suffix}/preflight.py",
                  BATCH / "design" / authority))
    if not files or any(not path.is_file() for path in files):
        raise RuntimeError("Missing source or design file")
    result = {"status": "SF013_CODE_AND_DESIGN_FROZEN_PRE_INJECTION",
              "case_id": "SF-013", "attempt_id": "SF-013-ATTEMPT-02" if suffix else
              "SF-013-ATTEMPT-01", "at_utc": datetime.now(timezone.utc).isoformat(),
              "source_sha256": {str(path): sha(path) for path in sorted(files)},
              "selected_mechanism": "POD_MEMORY_32M_300",
              "original_cpu_candidate": "BLOCKED_UNQUALIFIED",
              "prior_mechanism_proof_sha256": sha(
                  OPS / "qualification/COMP-SF01-POD-300/decision.json"),
              "prior_adapter_sha256": sha(OPS / "scripts/competition_adapter.py"),
              "workload_profile": "LOW_STABLE",
              "admission_contract_frozen_before_fault": True,
              "faults_injected": 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "files": len(files),
                      "sha256": sha(args.output), "path": str(args.output)}))


if __name__ == "__main__":
    main()
