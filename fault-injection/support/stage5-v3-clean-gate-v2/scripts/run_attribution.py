"""Bounded A/B/C no-fault CPU attribution with fresh five-host checks."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


LOCAL = Path("stage5-v3-clean-gate-v2")
ROOT = Path("/root/stage5-v3-clean-gate-v2")
OLD_LOCAL = Path("stage5-v3-autonomous-preparation")
sys.path.insert(0, str(LOCAL / "scripts"))
from host_io import HOSTS, remote, scp_to, ssh_cmd  # noqa: E402


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError("Refusing overwrite: " + str(path))
    path.write_text(json.dumps(value, indent=2) + "\n")


def health(name):
    name = name + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    residue = LOCAL / "snapshots" / (name + "-residue.json")
    command = ["python3", str(OLD_LOCAL / "scripts/audit_hosts.py")]
    for host, (ip, socket) in HOSTS.items():
        command.extend(["--host", ",".join(("k8s-master" if host == "master"
                        else host, ip, socket))])
    subprocess.run([*command, "--output", str(residue)], check=True, timeout=120)
    scp_to("master", residue, ROOT / "snapshots" / residue.name)
    target = ROOT / "snapshots" / (name + "-health.json")
    remote("master", "python3", "/root/stage5-v2-cross-layer-rca/scripts/health_gate.py",
           "--root", "/root/stage5-v2-cross-layer-rca", "--stage4f-root",
           "/root/stage4f-cross-layer-validation", "--residue",
           ROOT / "snapshots" / residue.name, "--output", target, timeout=120)
    value = json.loads(remote("master", "cat", target))
    if value["status"] != "STAGE5_BASE_HEALTH_PASS":
        raise RuntimeError("Health gate failed at " + name)
    free = int(remote("master", "df", "-B1", "--output=avail", "/root")
               .splitlines()[-1].strip())
    if free < 8 * 1024 ** 3:
        raise RuntimeError("Master free space below 8 GiB")
    return target


def host_snapshot(condition, phase):
    results = {"observed_utc": datetime.now(timezone.utc).isoformat(), "hosts": {}}
    for host in HOSTS:
        raw = remote(host, "cat", "/proc/stat", timeout=20)
        cpu = next(line for line in raw.splitlines() if line.startswith("cpu "))
        processes = remote(host, "ps", "-eo", "pid=,comm=,time=,args=", timeout=20)
        results["hosts"][host] = {"cpu_total_ticks": [int(x) for x in cpu.split()[1:]],
                                   "process_table": processes.splitlines()}
    path = LOCAL / "runs" / condition / (phase + "-host-snapshot.json")
    save(path, results)
    scp_to("master", path, ROOT / "runs" / condition / path.name)


def run_one(condition):
    health(condition + "-pre")
    remote("master", "mkdir", "-p", ROOT / "runs" / condition)
    stdout = (LOCAL / "runs" / condition / "runner.stdout")
    stdout.parent.mkdir(parents=True, exist_ok=True)
    if stdout.exists():
        raise ValueError("Condition already attempted")
    with stdout.open("x") as out, stdout.with_suffix(".stderr").open("x") as err:
        process = subprocess.Popen(ssh_cmd("master", "timeout", "360", "python3",
            ROOT / "scripts/run_components.py", "--condition", condition,
            "--output", ROOT / "runs" / condition / "load"), stdout=out, stderr=err)
        try:
            for _ in range(30):
                try:
                    schedule = json.loads(remote("master", "cat", ROOT / "runs" /
                        condition / "load/schedule.json", timeout=5))
                    break
                except Exception:
                    if process.poll() is not None:
                        raise RuntimeError("Component runner ended before schedule")
                    time.sleep(1)
            else:
                raise RuntimeError("Component schedule absent")
            save(LOCAL / "runs" / condition / "schedule.json", schedule)
            start = datetime.fromisoformat(schedule["scheduled_start_utc"]).timestamp()
            time.sleep(max(0, start + 60 - time.time()))
            host_snapshot(condition, "analysis-start")
            time.sleep(max(0, start + 300 - time.time()))
            host_snapshot(condition, "analysis-end")
            if process.wait(timeout=60):
                raise RuntimeError("Component runner failed: " + condition)
            cpu_root = ROOT / "runs" / condition / "cpu-window"
            remote("master", "python3", ROOT / "scripts/collect_cpu_window.py",
                "--start", schedule["analysis_start_utc"], "--end",
                schedule["scheduled_end_utc"], "--output", cpu_root, timeout=120)
            health(condition + "-post")
            save(LOCAL / "runs" / condition / "result.json", {
                "status": "CONDITION_ARCHIVED", "condition": condition,
                "fault_injected": False, "source_schedule_sha256": hashlib.sha256(
                    (LOCAL / "runs" / condition / "schedule.json").read_bytes()).hexdigest()})
            scp_to("master", LOCAL / "runs" / condition / "result.json",
                   ROOT / "runs" / condition / "orchestrator-result.json")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", choices=("A_background", "B_state_machine",
        "C_state_machine_plus_controls"), required=True)
    args = parser.parse_args()
    remote("master", "mkdir", "-p", ROOT / "snapshots", ROOT / "runs")
    prereg = LOCAL / "design/no-fault-study-prereg.yaml"
    if hashlib.sha256(prereg.read_bytes()).hexdigest() != remote(
            "master", "sha256sum", ROOT / "design/no-fault-study-prereg.yaml").split()[0]:
        raise RuntimeError("Preregistration differs on Master")
    run_one(args.condition)


if __name__ == "__main__":
    main()
