#!/usr/bin/env python3
"""Run one preregistered, bounded, no-fault ordinary-business Canary."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

import yaml


STATE = Path("/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py")
CONTROLS = Path("/root/stage5-v3-autonomous-preparation/scripts/fixed_control_load_c3.py")
MACHINE = Path("/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml")
PROFILES = Path("/root/stage5-v3-autonomous-preparation/design/load-profiles-c3.yaml")
PEOPLE = Path("/root/cross-layer-workload-generation/design/people.json")
BASE_URL = "http://10.104.177.128:8080"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def admin_stats():
    with urllib.request.urlopen("http://127.0.0.1:10000/stats?filter=tracing|opentelemetry_collector_grpc", timeout=10) as response:
        lines = response.read().decode().splitlines()
    return {key.strip(): int(value.strip()) for line in lines if ": " in line
            for key, value in [line.rsplit(": ", 1)] if value.strip().isdigit()}


def collector_state(plan_path, applied_path):
    plan = read(plan_path)
    applied = read(applied_path)
    config = json.loads(subprocess.check_output(["kubectl", "-n", "otel-demo", "get",
        "configmap", "otel-collector", "-o", "json"], text=True, timeout=25))
    pods = json.loads(subprocess.check_output(["kubectl", "-n", "otel-demo", "get",
        "pods", "-o", "json"], text=True, timeout=25))["items"]
    matched = [pod for pod in pods if pod["metadata"]["name"].startswith("otel-collector-")
        and pod["metadata"].get("deletionTimestamp") is None and
        pod["status"].get("phase") == "Running" and pod["status"].get(
            "containerStatuses") and all(row.get("ready") for row in pod[
                "status"]["containerStatuses"])]
    if (len(matched) != 1 or plan["status"] !=
            "SINGLE_COLLECTOR_CANDIDATE_PLANNED_NOT_APPLIED" or
            applied["status"] != "COLLECTOR_CANDIDATE_APPLIED" or
            config["data"]["relay"] != plan["new_relay"] or
            matched[0]["metadata"]["uid"] != applied["state"]["pod_uid"]):
        raise ValueError("Candidate Collector state drift")
    pod = matched[0]
    return {"pod": pod["metadata"]["name"], "uid": pod["metadata"]["uid"],
        "node": pod["spec"].get("nodeName"),
        "restarts": sum(row.get("restartCount", 0) for row in pod["status"]["containerStatuses"]),
        "config_relay_sha256": hashlib.sha256(config["data"]["relay"].encode()).hexdigest()}


def stream_seed_namespaces_disjoint(candidate_seed, previous_runs, offsets):
    candidate = {candidate_seed, *(candidate_seed + offset for offset in offsets)}
    return all(not candidate & {previous["seed"], *(previous["seed"] + offset
        for offset in offsets)} for previous in previous_runs)


def precheck(prereg, role, health, residue):
    if (prereg["status"] != "PREREGISTERED_BEFORE_NEW_CANARY" or
            prereg["fault_injection"] != "forbidden" or
            prereg["workload"]["duration_seconds"] != 180 or
            health["status"] != "GLOBAL_SAFETY_PASS" or
            residue["status"] != "HOST_RESIDUE_PASS"):
        raise ValueError("Canary or independent safety gate is not valid")
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(health["captured_at_utc"])).total_seconds()
    if not 0 <= age <= 180:
        raise ValueError("Global Safety gate is stale")
    allowed = {item["role"]: item for item in prereg["canaries"]}
    if role not in allowed:
        raise ValueError("Unregistered Canary role")
    candidate_seed = allowed[role]["seed"]
    offsets = [item["seed_offset"] for item in prereg["workload"]["control_streams"]]
    if not stream_seed_namespaces_disjoint(candidate_seed, prereg.get("previous_runs", []), offsets):
        raise ValueError("Cross-run state/control seed collision")
    profile = yaml.safe_load(PROFILES.read_text())["profiles"]["MEDIUM"]
    machine = yaml.safe_load(MACHINE.read_text())
    if (profile["arrival_rate_rps"] != 1.4 or
            {key: value["weight"] for key, value in machine["workflows"].items()}
            != prereg["workload"]["workflow_weights"]):
        raise ValueError("Workload changed from preregistration")
    if not role.startswith("collector_repair_"):
        raise ValueError("This runner accepts only registered Collector repair roles")
    timer = subprocess.run(["systemctl", "is-active", prereg["rollout_timer"]],
                           text=True, capture_output=True, timeout=10)
    if timer.returncode or timer.stdout.strip() != "active":
        raise ValueError("Independent Collector rollback timer is not active")
    return allowed[role]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--health", type=Path, required=True)
    parser.add_argument("--residue", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--applied", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite Canary")
    prereg = yaml.safe_load(args.prereg.read_text())
    case = precheck(prereg, args.role, read(args.health), read(args.residue))
    collector_before = collector_state(args.plan, args.applied)
    stats_before = admin_stats()
    args.output.mkdir(parents=True)
    start = datetime.now(timezone.utc) + timedelta(seconds=20)
    duration = prereg["workload"]["duration_seconds"]
    sources = (args.prereg, args.health, args.residue, args.plan, args.applied,
               STATE, CONTROLS, MACHINE, PROFILES, PEOPLE, Path(__file__))
    schedule = {"id": case["id"], "role": args.role, "seed": case["seed"],
        "scheduled_start_utc": start.isoformat(),
        "scheduled_end_utc": (start + timedelta(seconds=duration)).isoformat(),
        "fault_injected": False, "formal_case": False,
        "source_sha256": {str(path): sha(path) for path in sources},
        "envoy_admin_stats_before": stats_before,
        "collector_before": collector_before}
    (args.output / "schedule.json").write_text(json.dumps(schedule, indent=2) + "\n")
    commands = {
        "state-machine": ["python3", "-B", str(STATE), "--machine", str(MACHINE),
            "--profiles", str(PROFILES), "--people", str(PEOPLE), "--profile", "MEDIUM",
            "--seed", str(case["seed"]), "--duration", str(duration),
            "--start-utc", start.isoformat(), "--base-url", BASE_URL,
            "--output", str(args.output / "state-machine")],
        "controls": ["python3", "-B", str(CONTROLS), "--plan", str(args.prereg),
            "--seed", str(case["seed"]), "--duration", str(duration),
            "--start-utc", start.isoformat(), "--base-url", BASE_URL,
            "--output", str(args.output / "controls")]}
    processes = []
    try:
        for name, command in commands.items():
            stdout = (args.output / (name + ".stdout")).open("x")
            stderr = (args.output / (name + ".stderr")).open("x")
            processes.append((name, subprocess.Popen(command, stdout=stdout, stderr=stderr), stdout, stderr))
        codes = {}
        for name, process, stdout, stderr in processes:
            codes[name] = process.wait(timeout=duration + 100)
            stdout.close()
            stderr.close()
        stats_after = admin_stats()
        collector_after = collector_state(args.plan, args.applied)
        manifests = {}
        for name in commands:
            path = args.output / name / "result-manifest.json"
            manifests[name] = read(path) if path.exists() else None
        status = "CANARY_WORKLOAD_COMPLETE" if all(code == 0 for code in codes.values()) and all(
            item and item["status"] in ("LOAD_PASS", "CONTROL_STREAMS_COMPLETE")
            for item in manifests.values()) else "CANARY_WORKLOAD_FAILED"
        result = {"status": status, "exit_codes": codes,
            "manifest_statuses": {key: item["status"] if item else None for key, item in manifests.items()},
            "envoy_admin_stats_after": stats_after,
            "collector_after": collector_after,
            "completed_utc": datetime.now(timezone.utc).isoformat(),
            "fault_injected": False}
        (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result), flush=True)
        if status != "CANARY_WORKLOAD_COMPLETE":
            raise SystemExit(1)
    finally:
        for _, process, stdout, stderr in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
            if not stdout.closed:
                stdout.close()
            if not stderr.closed:
                stderr.close()


if __name__ == "__main__":
    main()
