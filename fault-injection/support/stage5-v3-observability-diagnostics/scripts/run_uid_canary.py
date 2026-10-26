"""Run one preregistered no-fault state-machine UID canary."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import yaml


ROOT = Path("/root/stage5-v3-observability-diagnostics")
GENERATOR = Path(
    "/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py")
PROFILES = Path("/root/stage5-v3-autonomous-preparation/design/load-profiles-c3.yaml")
PEOPLE = Path("/root/cross-layer-workload-generation/design/people.json")
MACHINE = ROOT / "snapshots/reference/workload-state-machine-c3.yaml"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def kube(*args):
    return subprocess.check_output(["kubectl", "-n", "otel-demo", *args],
                                   text=True, timeout=20)


def verify_prerequisites(prereg, plan_path, health_path):
    if prereg["status"] != "PREREGISTERED_BEFORE_UID_ROLLOUT" or \
            prereg["canary"]["duration_seconds"] != 180 or \
            prereg["fault_injection"] != "forbidden":
        raise ValueError("UID canary is not preregistered")
    hashes = prereg["source_sha256"]
    for path, expected in ((Path(__file__), hashes["run_uid_canary_py"]),
                           (ROOT / "scripts/audit_uid_canary.py",
                            hashes["audit_uid_canary_py"]),
                           (ROOT / "scripts/uid_rollout.py", hashes["uid_rollout_py"]),
                           (GENERATOR, hashes["workload_generator_py"]),
                           (PROFILES, hashes["load_profiles_c3_yaml"]),
                           (PEOPLE, hashes["synthetic_people_json"]),
                           (MACHINE, hashes["state_machine_yaml"]),
                           (plan_path, hashes["repair_plan_json"])):
        if sha(path) != expected:
            raise ValueError("Canary source drift: " + str(path))
    health = read(health_path)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(health[
        "captured_at_utc"])).total_seconds()
    if (health["status"] != "GLOBAL_SAFETY_PASS" or not 0 <= age <=
            prereg["preconditions"]["fresh_global_safety_pass_max_age_seconds"]):
        raise ValueError("No recent clean Global Safety decision")
    timer = subprocess.run(["systemctl", "is-active",
        prereg["rollout"]["independent_rollback_unit"] + ".timer"],
        capture_output=True, text=True, timeout=10)
    if timer.returncode or timer.stdout.strip() != "active":
        raise ValueError("Independent rollback timer is not active")
    plan = read(plan_path)
    if set(plan["services"]) != set(prereg["targets"]):
        raise ValueError("Canary targets differ from repair plan")
    pods = json.loads(kube("get", "pods", "-o", "json"))["items"]
    for service in prereg["targets"]:
        live = json.loads(kube("get", "deployment", service, "-o", "json"))
        expected = plan["services"][service]
        matching = [pod for pod in pods if pod["metadata"]["labels"].get(
            "opentelemetry.io/name") == service and pod["status"].get(
                "phase") == "Running" and pod["metadata"].get(
                    "deletionTimestamp") is None and pod["status"].get(
                    "containerStatuses") and all(row.get("ready") for row in pod[
                        "status"]["containerStatuses"])]
        if (live["metadata"]["uid"] != expected["deployment_uid"] or
                live["spec"]["template"]["spec"]["containers"][0]["env"] !=
                    expected["new_env"] or live["status"].get("readyReplicas") != 1
                or len(matching) != 1 or matching[0]["spec"]["containers"][0][
                    "env"] != expected["new_env"]):
            raise ValueError(service + " is not one updated Ready Pod")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--health", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite Canary")
    prereg = yaml.safe_load(args.prereg.read_text())
    verify_prerequisites(prereg, args.plan, args.health)
    args.output.mkdir(parents=True)
    start = datetime.now(timezone.utc) + timedelta(seconds=20)
    schedule = {"id": prereg["canary"]["id"],
        "seed": prereg["canary"]["seed"],
        "scheduled_start_utc": start.isoformat(),
        "scheduled_end_utc": (start + timedelta(seconds=180)).isoformat(),
        "fault_injected": False, "formal_case": False,
        "prereg_sha256": sha(args.prereg), "plan_sha256": sha(args.plan),
        "health_sha256": sha(args.health)}
    with (args.output / "schedule.json").open("x") as stream:
        json.dump(schedule, stream, indent=2)
        stream.write("\n")
    command = ["python3", "-B", str(GENERATOR), "--machine", str(MACHINE),
        "--profiles", str(PROFILES), "--people", str(PEOPLE), "--profile",
        "MEDIUM", "--seed", str(schedule["seed"]), "--duration", "180",
        "--start-utc", start.isoformat(), "--base-url",
        "http://10.104.177.128:8080", "--output", str(args.output / "load")]
    try:
        with (args.output / "generator.stdout").open("x") as stdout, (
                args.output / "generator.stderr").open("x") as stderr:
            status = subprocess.run(command, stdout=stdout, stderr=stderr,
                                    timeout=280).returncode
    except subprocess.TimeoutExpired:
        status = "TIMEOUT"
    manifest = args.output / "load/result-manifest.json"
    result = {"status": "CANARY_WORKLOAD_COMPLETED" if status == 0 and
        manifest.is_file() and read(manifest).get("status") == "LOAD_PASS" else
        "CANARY_WORKLOAD_FAILED", "exit_code": status,
        "load_manifest_sha256": sha(manifest) if manifest.is_file() else None,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "fault_injected": False}
    with (args.output / "result.json").open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result))
    if result["status"] != "CANARY_WORKLOAD_COMPLETED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
