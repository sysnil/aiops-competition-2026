"""Score a complete independent no-fault window under old and candidate rules."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path("/root/stage5-v3-clean-gate-v2")
OLD = Path("/root/stage5-v3-autonomous-preparation")


def run_step(name, command, log_root):
    result = subprocess.run([str(value) for value in command],
                            capture_output=True, text=True, timeout=600)
    with (log_root / (name + ".stdout")).open("x") as stream:
        stream.write(result.stdout)
    with (log_root / (name + ".stderr")).open("x") as stream:
        stream.write(result.stderr)
    if result.returncode:
        raise RuntimeError(name + " failed: " + result.stderr[-600:])


def one_file(pattern):
    rows = list(ROOT.glob(pattern))
    if len(rows) != 1:
        raise ValueError("Need exactly one post-run health artifact: " + pattern)
    return rows[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", choices=("S5V3AP-CGV2-VAL-1",
                                         "S5V3AP-CGV2-VAL-2"),
                        required=True)
    args = parser.parse_args()
    run = ROOT / "runs" / args.id
    lifecycle = json.loads((run / "result.json").read_text())
    if lifecycle["status"] != "CLEAN_LIFECYCLE_PASS" or lifecycle["fault_injected"]:
        raise ValueError("No complete no-fault lifecycle")
    if any(json.loads((run / (host + "-cleanup.json")).read_text())[
            "status"] != "ATTEMPT_PROCESSES_STOPPED" for host in
            ("master", "k8s-worker3")):
        raise ValueError("Cleanup not verified")
    if json.loads((run / "capture-pipeline/pipeline-result.json").read_text())[
            "status"] != "CAPTURE_PIPELINE_PASS":
        raise ValueError("Capture incomplete")
    logs = ROOT / "runtime" / (args.id + "-postprocess")
    if logs.exists():
        raise ValueError("Refusing postprocess overwrite")
    logs.mkdir(parents=True)
    run_step("source-lock", [sys.executable,
        ROOT / "scripts/clean_source_lock.py", "--verify", "--output",
        ROOT / "design/validation-source-lock.json"], logs)
    analysis = ROOT / "results" / (args.id + "-analysis.json")
    run_step("analysis", [sys.executable, ROOT / "scripts/analyze_clean_v2.py",
        "--run", run, "--output", analysis], logs)
    run_step("leak-audit", [sys.executable, OLD /
        "scripts/audit_public_boundary.py", "--run", run, "--output",
        ROOT / "results" / (args.id + "-public-leak-audit.json")], logs)
    resource = ROOT / "results" / (args.id + "-resource-pressure")
    run_step("old-resource-gate", [sys.executable, OLD /
        "scripts/assess_resource_pressure.py", "--run", run, "--plan",
        OLD / "design/load-plan-c3.yaml", "--output", resource], logs)
    health = one_file("snapshots/" + args.id + "-post-*-health.json")
    residue = Path(str(health).replace("-health.json", "-residue.json"))
    if not residue.exists():
        raise ValueError("Post-run residue absent")
    old_decision = ROOT / "results" / (args.id + "-old-clean-decision.json")
    run_step("old-clean-gate", [sys.executable, OLD / "scripts/assess_clean.py",
        "--analysis", analysis, "--health", health, "--residue", residue,
        "--resource-pressure", resource / "decision.json", "--output",
        old_decision], logs)
    candidate = ROOT / "results" / (args.id + "-candidate-rules.json")
    run_step("candidate-only", [sys.executable, ROOT / "scripts/clean_rules.py",
        "--run", run, "--output", candidate], logs)
    run_step("control-statistics", [sys.executable,
        ROOT / "scripts/control_statistics.py", "--run", run, "--output",
        ROOT / "results" / (args.id + "-control-statistics.json")], logs)
    timing = ROOT / "results" / (args.id + "-trace-timing.json")
    run_step("trace-timing", [sys.executable,
        ROOT / "scripts/audit_trace_timing.py", "--run", run,
        "--output", timing], logs)
    trace_v2 = ROOT / "results" / (args.id + "-trace-v2.json")
    run_step("trace-v2", [sys.executable, ROOT / "scripts/evaluate_trace_v2.py",
        "--run", run, "--output", trace_v2], logs)
    candidate_v2 = ROOT / "results" / (args.id + "-candidate-v2.json")
    run_step("candidate-v2", [sys.executable, ROOT /
        "scripts/evaluate_clean_v2.py", "--run", run,
        "--analysis", analysis, "--cpu-safety", run / "cpu-safety/manifest.json",
        "--trace-v2", trace_v2, "--leak-audit", ROOT / "results" /
        (args.id + "-public-leak-audit.json"), "--output", candidate_v2], logs)
    proposed = json.loads(candidate_v2.read_text())
    value = {"status": "CLEAN_POSTPROCESS_COMPLETE", "run_id": args.id,
        "old_rule_status": json.loads(old_decision.read_text())["status"],
        "candidate_E_hybrid_B_and_C": json.loads(candidate.read_text())[
            "combined_rules"]["E_hybrid_B_and_C"],
        "candidate_approved": False, "independent_global_safety_required": True,
        "candidate_v2_B_overall": proposed["B_overall"],
        "candidate_v2_E_overall": proposed["E_overall"],
        "trace_v2_status": json.loads(trace_v2.read_text())["status"],
        "rule_lock_sha256": hashlib.sha256((ROOT /
            "design/clean-rule-candidate-lock.json").read_bytes()).hexdigest(),
        "at_utc": datetime.now(timezone.utc).isoformat()}
    with (logs / "postprocess-summary.json").open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps(value))


if __name__ == "__main__":
    main()
