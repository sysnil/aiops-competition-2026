"""Independent 10+5+10 no-fault calibration or validation window."""

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


LOCAL = Path("stage5-v3-clean-gate-v2")
ROOT = Path("/root/stage5-v3-clean-gate-v2")
OLD = Path("/root/stage5-v3-autonomous-preparation")
OLD_LOCAL = Path("stage5-v3-autonomous-preparation")
sys.path.insert(0, str(LOCAL / "scripts"))
from host_io import remote, scp_to, scp_from, ssh_cmd, finish_processes  # noqa: E402
from run_attribution import health, save  # noqa: E402


REGISTERED = {"S5V3AP-CGV2-VAL-1": 861201,
              "S5V3AP-CGV2-VAL-2": 861202}


def wait_until(utc):
    time.sleep(max(0, utc.timestamp() - time.time()))


def probe_timeout_seconds(offset, count):
    return offset + count * 5 + 90


def probe_manifest_valid(manifest, planned, observed_sha256):
    return (manifest.get("status") == "QUALIFICATION_PROBES_COMPLETE" and
            manifest.get("planned_samples") == planned and
            manifest.get("interval_seconds") == 5 and
            sum(manifest.get("status_counts", {}).values()) == planned and
            manifest.get("observations_sha256") == observed_sha256 and
            manifest.get("timeout_imputation") is None)


def verify_remote_probe(root, phase, planned):
    probe_root = root / ("probe-" + phase)
    manifest = json.loads(remote("k8s-worker3", "cat", probe_root / "manifest.json"))
    digest = remote("k8s-worker3", "sha256sum", probe_root /
                    "observations.jsonl").split()[0]
    if not probe_manifest_valid(manifest, planned, digest):
        raise RuntimeError("Probe manifest invalid: " + phase)


def probe_schedule_valid(rows, start, offset, count):
    if len(rows) != count:
        return False
    for index, row in enumerate(rows):
        expected = start.timestamp() + offset + index * 5
        try:
            actual = datetime.fromisoformat(row["scheduled_utc"]).timestamp()
        except (KeyError, ValueError):
            return False
        if (row.get("index") != index or abs(actual - expected) > .01 or
                row.get("status") == "SUCCESS" and row.get(
                    "start_lag_seconds", 2) > 1):
            return False
    return True


def wait_for_window(start, processes, root):
    verified_probes = set()
    planned = {"probe-before": 120, "probe-during": 60, "probe-after": 120}
    end = start.timestamp() + 1500
    next_safety_check = 0
    while time.time() < end:
        if time.time() >= next_safety_check:
            safety_tick()
            next_safety_check = time.time() + 60
        for name, process in processes.items():
            code = process.poll()
            if code is not None and code != 0:
                raise RuntimeError(name + " exited with failure: " + str(code))
            if code == 0 and name in planned and name not in verified_probes:
                verify_remote_probe(root, name.split("-", 1)[1], planned[name])
                verified_probes.add(name)
        time.sleep(min(5, max(0, end - time.time())))


def safety_tick():
    nodes = json.loads(remote("master", "kubectl", "get", "nodes", "-o", "json"))
    if len(nodes["items"]) != 4 or any(
            {row["type"]: row["status"] for row in item["status"]["conditions"]}
            .get("Ready") != "True" or any(
                {row["type"]: row["status"] for row in item["status"]["conditions"]}
                .get(kind) != "False" for kind in
                ("DiskPressure", "MemoryPressure", "PIDPressure"))
            for item in nodes["items"]):
        raise RuntimeError("SAFETY_ABORT: node condition changed")
    ds = json.loads(remote("master", "kubectl", "-n", "otel-demo", "get",
                           "ds", "otel-infra-agent-agent", "-o", "json"))
    if ds["status"].get("numberReady") != 4:
        raise RuntimeError("SAFETY_ABORT: infra agent unavailable")
    operator = json.loads(remote("master", "kubectl", "-n", "tigera-operator",
                                 "get", "deploy", "tigera-operator", "-o", "json"))
    if operator["status"].get("readyReplicas") != 1:
        raise RuntimeError("SAFETY_ABORT: Tigera operator unavailable")
    free = int(remote("master", "df", "-B1", "--output=avail", "/")
               .splitlines()[-1])
    if free < 21_000_000_000:
        raise RuntimeError("SAFETY_ABORT: storage below eviction floor plus margin")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True, choices=REGISTERED)
    parser.add_argument("--rule-lock", type=Path, required=True)
    parser.add_argument("--source-lock", type=Path, required=True)
    args = parser.parse_args()
    if not args.rule_lock.is_file() or not args.source_lock.is_file():
        raise ValueError("Validation requires candidate and source locks")
    lock = json.loads(args.rule_lock.read_text())
    if lock.get("status") != "PROPOSED_NOT_APPROVED_LOCKED_BEFORE_VALIDATION":
        raise ValueError("Candidate rule lock is absent")
    master_lock = ROOT / "design/clean-rule-candidate-lock.json"
    if hashlib.sha256(args.rule_lock.read_bytes()).hexdigest() != remote(
            "master", "sha256sum", master_lock).split()[0]:
        raise ValueError("Candidate rule lock differs on Master")
    source = LOCAL / "design/validation-prereg.yaml"
    expected = remote("master", "sha256sum", ROOT /
                      "design/validation-prereg.yaml").split()[0]
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise ValueError("Preregistration differs between Mac and Master")
    if hashlib.sha256(args.source_lock.read_bytes()).hexdigest() != remote(
            "master", "sha256sum", ROOT / "design/validation-source-lock.json"
            ).split()[0]:
        raise ValueError("Source lock differs on Master")
    remote("master", "python3", ROOT / "scripts/clean_source_lock.py",
           "--verify", "--output", ROOT / "design/validation-source-lock.json")
    root = ROOT / "runs" / args.id
    local = LOCAL / "runs" / args.id
    if local.exists():
        raise ValueError("Attempt already archived")
    local.mkdir(parents=True)
    remote("master", "mkdir", "-p", root)
    remote("k8s-worker3", "mkdir", "-p", root, ROOT / "scripts")
    scp_to("k8s-worker3", LOCAL / "scripts/stop_calibration_attempt.py",
           ROOT / "scripts/stop_calibration_attempt.py")
    if remote("k8s-worker3", "sha256sum", ROOT /
              "scripts/stop_calibration_attempt.py").split()[0] != remote(
              "master", "sha256sum", ROOT / "scripts/stop_calibration_attempt.py"
              ).split()[0]:
        raise RuntimeError("Worker3 cleanup script differs from Master")
    health_path = health(args.id + "-pre")
    unit = "s5v3bc-probe-" + args.id.lower()
    if ":5201" in remote("k8s-worker2", "ss", "-ltn", "sport", "=", ":5201"):
        raise RuntimeError("Probe receiver port already occupied")
    receiver = False
    processes = {}
    completed = False
    try:
        remote("k8s-worker2", "systemd-run", "--quiet", "--unit=" + unit,
               "/usr/bin/timeout", "1700", "/usr/bin/iperf3", "-s", "-B",
               "192.168.53.54", "-p", "5201")
        receiver = True
        processes["load"] = subprocess.Popen(ssh_cmd("master", "timeout", "1630",
            "python3", OLD / "scripts/run_load_pair_c3.py", "--plan",
            OLD / "design/load-plan-c3.yaml", "--health", health_path,
            "--run-id", args.id, "--seed", str(REGISTERED[args.id]),
            "--output", root / "load_pair", "--allow-errors"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(30):
            try:
                schedule = json.loads(remote("master", "cat", root /
                    "load_pair/schedule.json", timeout=5))
                break
            except Exception:
                if processes["load"].poll() is not None:
                    raise RuntimeError("Workload failed before schedule")
                time.sleep(1)
        else:
            raise RuntimeError("Schedule absent")
        save(local / "schedule.json", schedule)
        start = datetime.fromisoformat(schedule["scheduled_start_utc"])
        processes["capture"] = subprocess.Popen(ssh_cmd("master", "timeout", "1950",
            "python3", ROOT / "scripts/capture_pipeline_v3.py", "--start",
            start.isoformat(), "--output", root / "capture-pipeline"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        processes["cpu-safety"] = subprocess.Popen(ssh_cmd("master", "timeout",
            "1800", "python3", ROOT / "scripts/cpu_safety_sampler.py", "--start",
            start.isoformat(), "--duration", "1500", "--output",
            root / "cpu-safety"), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        for phase, offset, count in (("before", 1, 120), ("during", 601, 60),
                                     ("after", 901, 120)):
            processes["probe-" + phase] = subprocess.Popen(ssh_cmd("k8s-worker3",
                "timeout", str(probe_timeout_seconds(offset, count)), "python3",
                OLD / "scripts/probe_network.py",
                "--path", "NETWORK_PATH/k8s-worker3/k8s-worker2", "--source-ip",
                "192.168.53.55", "--destination-ip", "192.168.53.54",
                "--start-utc", (start + timedelta(seconds=offset)).isoformat(),
                "--samples", str(count), "--interval", "5", "--output",
                root / ("probe-" + phase)), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True)
        wait_until(start + timedelta(seconds=10))
        subprocess.run(["python3", str(LOCAL / "scripts/verify_business_routes.py"),
            "--mode", "qualification_clean", "--output", str(local /
            "route-proof.json")], check=True, timeout=120)
        scp_to("master", local / "route-proof.json", root / "route-proof.json")
        wait_for_window(start, processes, root)
        status = finish_processes(processes, 420)
        save(local / "process-results.json", status)
        for phase in ("before", "during", "after"):
            scp_from("k8s-worker3", root / ("probe-" + phase),
                     local / ("probe-" + phase), recursive=True)
            manifest = json.loads((local / ("probe-" + phase) /
                                   "manifest.json").read_text())
            digest = hashlib.sha256((local / ("probe-" + phase) /
                                     "observations.jsonl").read_bytes()).hexdigest()
            rows = [json.loads(line) for line in (local / ("probe-" + phase) /
                "observations.jsonl").read_text().splitlines() if line]
            offset = {"before": 1, "during": 601, "after": 901}[phase]
            if not probe_manifest_valid(manifest, 60 if phase == "during" else 120,
                                        digest) or not probe_schedule_valid(rows, start,
                                        offset, 60 if phase == "during" else 120):
                raise RuntimeError("Transferred probe archive invalid: " + phase)
            subprocess.run(["scp", "-q", "-r", "-o", "BatchMode=yes", "-o",
                "ControlPath=/tmp/stage4f-formal-ssh", str(local / ("probe-" + phase)),
                "root@192.168.53.52:" + str(root / ("probe-" + phase))],
                check=True, timeout=120)
        lifecycle = {"id": args.id, "seed": REGISTERED[args.id],
            "status": "CLEAN_LIFECYCLE_PASS" if all(r["exit_code"] == 0 for r in
                status.values()) else "CLEAN_LIFECYCLE_FAILED", "fault_injected": False,
            "formal_case_created": False, "workload_plan_sha256":
            schedule["plan_sha256"], "process_exit_codes": {k: v["exit_code"] for
                k, v in status.items()}}
        save(local / "result.json", lifecycle)
        scp_to("master", local / "result.json", root / "result.json")
        completed = True
        print(json.dumps(lifecycle), flush=True)
        if lifecycle["status"] != "CLEAN_LIFECYCLE_PASS":
            raise RuntimeError("No-fault lifecycle failed")
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        if receiver:
            subprocess.run(ssh_cmd("k8s-worker2", "systemctl", "stop", unit +
                ".service"), capture_output=True, timeout=20)
            subprocess.run(ssh_cmd("k8s-worker2", "systemctl", "reset-failed", unit +
                ".service"), capture_output=True, timeout=20)
        cleanup_errors = []
        for host in ("master", "k8s-worker3"):
            try:
                remote(host, "python3", ROOT / "scripts/stop_calibration_attempt.py",
                       "--id", args.id, "--output", root / (host + "-cleanup.json"),
                       timeout=40)
            except Exception as exc:
                cleanup_errors.append(host + ": " + str(exc))
        try:
            if not cleanup_errors:
                scp_from("k8s-worker3", root / "k8s-worker3-cleanup.json",
                         local / "k8s-worker3-cleanup.json")
                scp_to("master", local / "k8s-worker3-cleanup.json",
                       root / "k8s-worker3-cleanup.json")
        finally:
            health(args.id + "-post")
        if cleanup_errors:
            raise RuntimeError("Remote cleanup failed: " + "; ".join(cleanup_errors))
        if not completed:
            print(json.dumps({"status": "INCOMPLETE_ATTEMPT_PRESERVED",
                              "id": args.id}), flush=True)


if __name__ == "__main__":
    main()
