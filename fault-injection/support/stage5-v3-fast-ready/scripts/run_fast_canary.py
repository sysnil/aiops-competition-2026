#!/usr/bin/env python3
"""One bounded, no-fault state-machine Canary with a detached Trace service."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request

import yaml


ROOT = Path("/root/stage5-v3-fast-ready")
DATA = Path("/data/stage5-v3/fast-ready")
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


def save(path, value):
    if path.exists():
        raise ValueError("Refusing overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def kube(*args):
    return subprocess.check_output(["/usr/bin/kubectl", "--kubeconfig=/etc/kubernetes/admin.conf",
        "--context=kubernetes-admin@kubernetes", "-n", "otel-demo", *args],
        text=True, timeout=40)


def pod_uids():
    pods = json.loads(kube("get", "pods", "-o", "json"))["items"]
    names = ("cart", "checkout", "shipping", "quote", "frontend",
             "frontend-proxy", "recommendation")
    result = {name: [] for name in names}
    for pod in pods:
        name = pod["metadata"]["labels"].get("opentelemetry.io/name")
        if name in result and pod["status"].get("phase") == "Running" and all(
                row.get("ready") for row in pod["status"].get("containerStatuses", [])):
            result[name].append(pod["metadata"]["uid"])
    expected = {"cart": 1, "checkout": 1, "shipping": 1, "quote": 1,
                "frontend": 2, "frontend-proxy": 1, "recommendation": 2}
    if any(len(result[name]) != count for name, count in expected.items()):
        raise ValueError("Critical business Pod identity or replicas unstable")
    return {name: sorted(values) for name, values in result.items()}


def scalar(expression):
    url = "http://10.97.14.138:9090/api/v1/query?" + urllib.parse.urlencode({
        "query": expression})
    with urllib.request.urlopen(url, timeout=15) as response:
        data = json.load(response)
    rows = data["data"]["result"]
    if len(rows) != 1:
        return {"status": "UNKNOWN_MISSING_OR_AMBIGUOUS", "value": None}
    return {"status": "OBSERVABLE", "value": float(rows[0]["value"][1]),
            "scrape_timestamp": rows[0]["value"][0], "labels": rows[0]["metric"]}


def counters(pod):
    return {name: scalar(metric + '{k8s_pod_name="' + pod + '"' + extra + '}')
        for name, metric, extra in (
            ("receiver_refused_grpc", "otelcol_receiver_refused_spans_total",
             ',receiver="otlp",transport="grpc"'),
            ("receiver_refused_http", "otelcol_receiver_refused_spans_total",
             ',receiver="otlp",transport="http"'),
            ("limiter_refused", "otelcol_processor_memory_limiter_refused_spans_total",
             ',processor="memory_limiter"'),
            ("jaeger_sent", "otelcol_exporter_sent_spans_total",
             ',exporter="otlp_grpc/jaeger"'))}


def precheck(plan, health, residue):
    now = datetime.now(timezone.utc)
    if (plan["status"] != "PREREGISTERED_NO_FAULT_CANARY" or
            plan["seed"] not in (863101, 863301) or
            plan["workload"]["duration_seconds"] != 180 or
            plan["fault_injection"] != "forbidden" or
            health["status"] != "GLOBAL_SAFETY_PASS" or not health["canary_allowed"] or
            residue["status"] != "HOST_RESIDUE_PASS" or
            not 0 <= (now - datetime.fromisoformat(health["captured_at_utc"])).total_seconds() <= 180 or
            not 0 <= (now - datetime.fromisoformat(residue["at_utc"])).total_seconds() <= 180):
        raise ValueError("No-fault Canary preregistration or fresh safety gate failed")
    machine = yaml.safe_load(MACHINE.read_text())
    profile = yaml.safe_load(PROFILES.read_text())["profiles"]["MEDIUM"]
    if (profile["arrival_rate_rps"] != 1.4 or
            {name: row["weight"] for name, row in machine["workflows"].items()} !=
            plan["workload"]["workflow_weights"]):
        raise ValueError("State-machine workload differs from registration")
    config = json.loads(kube("get", "configmap", "otel-collector", "-o", "json"))
    if yaml.safe_load(config["data"]["relay"])["processors"]["memory_limiter"][
            "spike_limit_percentage"] != 25:
        raise ValueError("Original Collector configuration changed")
    gateway = [pod for pod in json.loads(kube("get", "pods", "-o", "json"))["items"]
        if pod["metadata"]["name"].startswith("otel-collector-") and
        pod["status"].get("phase") == "Running"]
    if len(gateway) != 1 or gateway[0]["status"]["containerStatuses"][0][
            "restartCount"] != 0:
        raise ValueError("Collector Pod unstable")
    return gateway[0]["metadata"]["name"], pod_uids()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--health", type=Path, required=True)
    parser.add_argument("--residue", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    plan, health, residue = read_yaml(args.prereg), read(args.health), read(args.residue)
    if (plan.get("disk", {}).get("uuid") !=
            "a35051a2-3d09-4b83-bbc0-3bb055e8a4f3" or
            plan["disk"].get("raw_run_root") != str(DATA / "runs") or
            plan["disk"].get("planned_raw_bytes") != 5368709120 or
            plan["disk"].get("root_floor_unchanged") is not True):
        raise ValueError("Data disk Canary pre-registration missing or changed")
    from data_disk_guard import assert_ready
    disk = assert_ready(args.output, plan["disk"]["planned_raw_bytes"])
    if not args.output.is_relative_to(DATA / "runs"):
        raise ValueError("Canary run must use the mounted data disk runs directory")
    gateway, identities = precheck(plan, health, residue)
    args.output.mkdir(parents=True)
    start = datetime.now(timezone.utc) + timedelta(seconds=30)
    end = start + timedelta(seconds=180)
    sources = (args.prereg, args.health, args.residue, STATE, CONTROLS,
               MACHINE, PROFILES, PEOPLE, Path(__file__),
               ROOT / "scripts/timed_trace_capture.py",
               ROOT / "scripts/capture_live_on_data_disk.py",
               ROOT / "scripts/data_disk_guard.py")
    schedule = {"id": plan["id"], "seed": plan["seed"],
        "scheduled_start_utc": start.isoformat(),
        "scheduled_end_utc": end.isoformat(),
        "gateway_pod": gateway, "pod_uids": identities,
        "collector_counters_before": counters(gateway),
        "data_disk_guard": disk,
        "source_sha256": {str(path): sha(path) for path in sources},
        "trace_service_unit": "s5v3fr-trace-disk-canary-20261013",
        "live_capture_service_unit": "s5v3fr-live-disk-canary-20261013",
        "live_capture_output": str(DATA / "capture" / plan["id"]),
        "fault_injected": False, "formal_case": False}
    save(args.output / "schedule.json", schedule)
    trace_output = args.output / "trace"
    service = subprocess.run(["systemd-run", "--unit=" + schedule["trace_service_unit"],
        "--property=User=root", "--property=WorkingDirectory=/",
        "--property=Environment=KUBECONFIG=/etc/kubernetes/admin.conf",
        "--property=RuntimeMaxSec=600", "/usr/bin/python3", "-B",
        str(ROOT / "scripts/timed_trace_capture.py"), "--run", str(args.output),
        "--prereg", str(args.prereg), "--output", str(trace_output)],
        capture_output=True, text=True, timeout=15)
    if service.returncode:
        save(args.output / "terminal.json", {"status": "TRACE_SERVICE_REGISTRATION_FAILED",
            "error": service.stderr[-500:], "fault_injected": False})
        raise RuntimeError("Cannot register independent trace service")
    live = subprocess.run(["systemd-run", "--unit=" + schedule[
        "live_capture_service_unit"], "--property=User=root",
        "--property=WorkingDirectory=/",
        "--property=Environment=KUBECONFIG=/etc/kubernetes/admin.conf",
        "--property=RuntimeMaxSec=480", "/usr/bin/python3", "-B",
        str(ROOT / "scripts/capture_live_on_data_disk.py"), "--run",
        str(args.output), "--output", schedule["live_capture_output"]],
        capture_output=True, text=True, timeout=15)
    if live.returncode:
        subprocess.run(["systemctl", "stop", schedule["trace_service_unit"] + ".service"],
            capture_output=True, text=True, timeout=15)
        save(args.output / "terminal.json", {"status": "LIVE_CAPTURE_SERVICE_REGISTRATION_FAILED",
            "error": live.stderr[-500:], "fault_injected": False})
        raise RuntimeError("Cannot register independent live capture service")
    commands = {
        "state-machine": ["/usr/bin/python3", "-B", str(STATE), "--machine", str(MACHINE),
            "--profiles", str(PROFILES), "--people", str(PEOPLE), "--profile", "MEDIUM",
            "--seed", str(plan["seed"]), "--duration", "180", "--start-utc",
            start.isoformat(), "--base-url", BASE_URL, "--output",
            str(args.output / "state-machine")],
        "controls": ["/usr/bin/python3", "-B", str(CONTROLS), "--plan",
            str(args.prereg), "--seed", str(plan["seed"]), "--duration", "180",
            "--start-utc", start.isoformat(), "--base-url", BASE_URL,
            "--output", str(args.output / "controls")]}
    processes = []
    result = {"status": "CANARY_WORKLOAD_FAILED", "trace_service_registered": True,
              "live_capture_service_registered": True,
              "trace_service_stdout": service.stdout.strip(),
              "live_capture_service_stdout": live.stdout.strip(),
              "fault_injected": False}
    try:
        for name, command in commands.items():
            stdout = (args.output / (name + ".stdout")).open("x")
            stderr = (args.output / (name + ".stderr")).open("x")
            processes.append((name, subprocess.Popen(command, stdout=stdout,
                stderr=stderr), stdout, stderr))
        codes = {}
        for name, process, stdout, stderr in processes:
            codes[name] = process.wait(timeout=300)
            stdout.close()
            stderr.close()
        manifests = {name: read(args.output / name / "result-manifest.json")
                     if (args.output / name / "result-manifest.json").exists() else None
                     for name in commands}
        result["exit_codes"] = codes
        result["manifests"] = manifests
        result["collector_counters_after"] = counters(gateway)
        result["pod_uids_unchanged"] = pod_uids() == identities
        complete = (all(code == 0 for code in codes.values()) and
            all(item and item["status"] in ("LOAD_PASS", "CONTROL_STREAMS_COMPLETE")
                for item in manifests.values()) and result["pod_uids_unchanged"])
        result["status"] = "CANARY_WORKLOAD_COMPLETE" if complete else "CANARY_WORKLOAD_FAILED"
    except Exception as error:
        result["error"] = str(error)
        raise
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
        result["completed_utc"] = datetime.now(timezone.utc).isoformat()
        save(args.output / "result.json", result)
        print(json.dumps({"status": result["status"],
                          "exit_codes": result.get("exit_codes"),
                          "error": result.get("error")}))
    if result["status"] != "CANARY_WORKLOAD_COMPLETE":
        raise SystemExit(1)


def read_yaml(path):
    return yaml.safe_load(path.read_text())


if __name__ == "__main__":
    main()
