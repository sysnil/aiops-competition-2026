"""Run one preregistered no-fault validation only behind fresh safety pass."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import yaml

from guarded_rehearsal_entry import authorize


ROOT = Path("stage5-v3-clean-gate-v2")
MASTER = Path("/root/stage5-v3-clean-gate-v2")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_entry(decision, prereg, run_id, free_bytes):
    return (authorize(decision) and prereg.get("status") ==
        "PREREGISTERED_BEFORE_VALIDATION" and
        any(row["id"] == run_id for row in prereg.get("validation", {}).get(
            "runs", [])) and free_bytes >= prereg["validation"][
            "master_storage_before_each_run_min_bytes"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True)
    parser.add_argument("--decision", type=Path, required=True)
    args = parser.parse_args()
    prereg_path = ROOT / "design/validation-prereg.yaml"
    prereg = yaml.safe_load(prereg_path.read_text())
    decision = json.loads(args.decision.read_text())
    check = subprocess.run(["ssh", "-S", "/tmp/stage4f-formal-ssh", "-o",
        "BatchMode=yes", "root@192.168.53.52", "df", "-B1", "--output=avail",
        "/"], capture_output=True, text=True, timeout=10, check=True)
    free = int(check.stdout.splitlines()[-1])
    if not valid_entry(decision, prereg, args.id, free):
        raise SystemExit("REFUSED_CLEAN: fresh Global Safety or capacity missing")
    lock_path = ROOT / "design/validation-source-lock.json"
    remote_lock = subprocess.run(["ssh", "-S", "/tmp/stage4f-formal-ssh",
        "-o", "BatchMode=yes", "root@192.168.53.52", "python3", "-B",
        str(MASTER / "scripts/clean_source_lock.py"), "--verify", "--output",
        str(MASTER / "design/validation-source-lock.json")], capture_output=True,
        text=True, timeout=30)
    if remote_lock.returncode or sha(lock_path) != subprocess.check_output(
            ["ssh", "-S", "/tmp/stage4f-formal-ssh", "-o", "BatchMode=yes",
             "root@192.168.53.52", "sha256sum",
             str(MASTER / "design/validation-source-lock.json")],
            text=True, timeout=10).split()[0]:
        raise SystemExit("REFUSED_CLEAN: source lock mismatch")
    log = ROOT / "runtime" / (args.id + "-runner.json")
    if log.exists():
        raise ValueError("Attempt already has an execution record")
    result = subprocess.run([sys.executable, "-B",
        str(ROOT / "scripts/run_full_clean.py"), "--id", args.id,
        "--rule-lock", str(ROOT / "design/clean-rule-candidate-lock.json"),
        "--source-lock", str(lock_path)],
        capture_output=True, text=True, timeout=2100)
    with log.open("x") as stream:
        json.dump({"run_id": args.id, "exit_code": result.returncode,
                   "stdout": result.stdout[-6000:], "stderr": result.stderr[-6000:],
                   "decision_sha256": sha(args.decision),
                   "source_lock_sha256": sha(lock_path), "at_utc":
                   datetime.now(timezone.utc).isoformat()}, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"run_id": args.id, "status": "LIFECYCLE_PASS" if
        result.returncode == 0 else "LIFECYCLE_FAILED", "exit_code": result.returncode}))
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
