#!/usr/bin/env python3
"""Run one preregistered 45-minute no-fault reliability window with monitoring."""

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


ROOT = Path("/root/stage5-v3-collector-reliability")
STATE = Path("/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py")
CONTROLS = Path("/root/stage5-v3-autonomous-preparation/scripts/fixed_control_load_c3.py")
MACHINE = Path("/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml")
PROFILES = Path("/root/stage5-v3-autonomous-preparation/design/load-profiles-c3.yaml")
PEOPLE = Path("/root/cross-layer-workload-generation/design/people.json")
PROCESS_STATE = ROOT / "runtime/continuous-processes.json"
PROM = "http://10.97.14.138:9090"
OPENSEARCH = "http://10.111.114.255:9200"
WORKERS = {"worker2": ("192.168.53.54", "/tmp/stage5v3-w2-archive.sock"),
           "worker3": ("192.168.53.55", "/tmp/stage5v3-w3-archive.sock")}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def get_json(url, body=None):
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


def metric(expression, optional=False):
    params = urllib.parse.urlencode({"query": expression})
    response = get_json(PROM + "/api/v1/query?" + params)
    rows = response["data"]["result"] if response.get("status") == "success" else []
    if optional and len(rows) == 0:
        return {"value": None, "sample_timestamp": None, "labels": {},
                "status": "SERIES_ABSENT_UNKNOWN_NOT_ZERO"}
    if len(rows) != 1:
        raise RuntimeError("Critical Prometheus series absent or duplicated: " + expression)
    return {"value": float(rows[0]["value"][1]),
            "sample_timestamp": rows[0]["value"][0], "labels": rows[0]["metric"],
            "status": "PRESENT"}


def filesystem_free(path="/"):
    return int(subprocess.check_output(["df", "-B1", "--output=avail", path],
                                       text=True, timeout=10).splitlines()[-1])


def worker_free(address, socket):
    output = subprocess.check_output(["ssh", "-o", "BatchMode=yes",
        "-o", "ControlPath=" + socket, "root@" + address,
        "df", "-B1", "--output=avail", "/data"], text=True, timeout=15)
    return int(output.splitlines()[-1])


def verify_infrastructure(gateway_name, gateway_uid, expected_relay,
                          expected_business_uids=None):
    nodes = json.loads(subprocess.check_output(["kubectl", "get", "nodes", "-o", "json"],
                                               text=True, timeout=20))["items"]
    node_checks = {}
    for node in nodes:
        conditions = {item["type"]: item["status"] for item in node["status"]["conditions"]}
        node_checks[node["metadata"]["name"]] = conditions
    if len(node_checks) != 4 or any(row.get("Ready") != "True" or row.get(
            "DiskPressure") != "False" or row.get("MemoryPressure") != "False"
            for row in node_checks.values()):
        raise RuntimeError("Four-node readiness or pressure gate failed")
    pod = json.loads(subprocess.check_output(["kubectl", "-n", "otel-demo", "get",
        "pod", gateway_name, "-o", "json"], text=True, timeout=20))
    if (pod["metadata"]["uid"] != gateway_uid or pod["status"].get("phase") != "Running"
            or not all(row.get("ready") and row.get("restartCount") == 0
                       for row in pod["status"].get("containerStatuses", []))):
        raise RuntimeError("Collector Pod changed or restarted")
    config = json.loads(subprocess.check_output(["kubectl", "-n", "otel-demo", "get",
        "configmap", "otel-collector", "-o", "json"], text=True, timeout=20))
    if hashlib.sha256(config["data"]["relay"].encode()).hexdigest() != expected_relay:
        raise RuntimeError("Collector config drift during frozen window")
    demo_pods = json.loads(subprocess.check_output(["kubectl", "-n", "otel-demo", "get",
        "pods", "-o", "json"], text=True, timeout=20))["items"]
    business = {service: sorted(item["metadata"]["uid"] for item in demo_pods
        if item["metadata"]["labels"].get("opentelemetry.io/name") == service
        and item["status"].get("phase") == "Running" and item["metadata"].get(
            "deletionTimestamp") is None and item["status"].get("containerStatuses")
        and all(row.get("ready") for row in item["status"]["containerStatuses"]))
        for service in ("cart", "checkout", "shipping", "quote", "frontend", "recommendation")}
    frontend_worker1 = [item["metadata"]["uid"] for item in demo_pods
        if item["metadata"]["labels"].get("opentelemetry.io/name") == "frontend" and
        item["spec"].get("nodeName") == "k8s-worker1" and item["metadata"]["uid"] in
        business["frontend"]]
    if any(not rows for rows in business.values()) or (expected_business_uids is not None
            and business != expected_business_uids) or len(frontend_worker1) != 1:
        raise RuntimeError("Business Pod UID or placement readiness changed")
    if not any(item["metadata"]["name"].startswith("jaeger-") and
            item["status"].get("phase") == "Running" and all(row.get("ready")
                for row in item["status"].get("containerStatuses", []))
            for item in demo_pods):
        raise RuntimeError("Jaeger Pod not Ready")
    node_metric_counts = {}
    node_metric_values = {}
    for name in ("k8s_node_cpu_usage", "k8s_node_memory_available_bytes",
                 "k8s_node_condition_ready"):
        response = get_json(PROM + "/api/v1/query?" + urllib.parse.urlencode({"query": name}))
        node_metric_counts[name] = len({row["metric"].get("k8s_node_name") or row[
            "metric"].get("node") for row in response["data"]["result"]})
        node_metric_values[name] = {row["metric"].get("k8s_node_name") or row[
            "metric"].get("node"): float(row["value"][1]) for row in response[
                "data"]["result"]}
        if node_metric_counts[name] != 4:
            raise RuntimeError("Four-node Prometheus metric coverage missing: " + name)
    os_health = get_json(OPENSEARCH + "/_cluster/health")
    if os_health.get("status") != "green":
        raise RuntimeError("OpenSearch health not green")
    with urllib.request.urlopen(PROM + "/-/ready", timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("Prometheus not Ready")
    return {"nodes": node_checks, "collector_pod_uid": gateway_uid,
            "opensearch": os_health.get("status"),
            "business_pod_uids": business, "node_metric_counts": node_metric_counts,
            "node_metric_values": node_metric_values,
            "frontend_worker1_uid": frontend_worker1[0],
            "master_free_bytes": filesystem_free(),
            "workers_free_bytes": {name: worker_free(*info) for name, info in WORKERS.items()}}


def verify_sources(prereg_path, health_path, residue_path, lock_path, plan_path,
                   applied_path, canary_ids):
    prereg = yaml.safe_load(prereg_path.read_text())
    health, residue, lock, plan, applied = [read(path) for path in
        (health_path, residue_path, lock_path, plan_path, applied_path)]
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(health["captured_at_utc"])).total_seconds()
    if (prereg["status"] != "PREREGISTERED_BEFORE_COLLECTOR_CHANGE" or
            prereg["workload"]["duration_seconds"] != 2700 or
            prereg["fault_injection"] != "forbidden" or
            health["status"] != "GLOBAL_SAFETY_PASS" or not 0 <= age <= 180 or
            residue["status"] != "HOST_RESIDUE_PASS" or
            lock["status"] != "COLLECTOR_RELIABILITY_SOURCES_LOCKED" or
            plan["status"] != "SINGLE_COLLECTOR_CANDIDATE_PLANNED_NOT_APPLIED" or
            applied["status"] != "COLLECTOR_CANDIDATE_APPLIED"):
        raise ValueError("Continous study prerequisites absent")
    if not lock["source_sha256"].get(str(prereg_path)) == sha(prereg_path):
        raise ValueError("Preregistration changed after source lock")
    for item in (Path(__file__), STATE, CONTROLS, MACHINE, PROFILES, PEOPLE):
        if lock["source_sha256"].get(str(item)) != sha(item):
            raise ValueError("Generator or monitoring source changed")
    timer = subprocess.run(["systemctl", "is-active", "s5v3cr-collector-rollback.timer"],
                           capture_output=True, text=True, timeout=10)
    if timer.returncode or timer.stdout.strip() != "active":
        raise ValueError("Independent Collector rollback timer inactive")
    canary_metric_unknown = False
    for run_id in canary_ids:
        audit = read(ROOT / "runs" / run_id / "trace-audit/audit.json")
        late = read(ROOT / "runs" / run_id / "late-audit/late-audit.json")
        if audit["status"] not in ("COLLECTOR_CANARY_PASS",
                "COLLECTOR_CANARY_COVERAGE_PASS_METRIC_UNKNOWN") or late[
                "status"] != "NO_CRITICAL_LATE_SPANS":
            raise ValueError("Repair Canary prerequisite failed: " + run_id)
        canary_metric_unknown |= audit["status"] == "COLLECTOR_CANARY_COVERAGE_PASS_METRIC_UNKNOWN"
    if canary_metric_unknown and not prereg.get("diagnostic_continuous_allowed_if_limiter_counter_absent_but_grpc_receiver_counter_present"):
        raise ValueError("Diagnostic continuation not preregistered")
    expected = applied["state"]
    infrastructure = verify_infrastructure(expected["pod_name"], expected["pod_uid"],
                                           plan["new_relay_sha256"])
    if (infrastructure["master_free_bytes"] < 46000000000 or any(
            free < 50000000000 for free in infrastructure["workers_free_bytes"].values())):
        raise ValueError("Pre-window storage budget unavailable")
    profile = yaml.safe_load(PROFILES.read_text())["profiles"]["MEDIUM"]
    machine = yaml.safe_load(MACHINE.read_text())
    if (profile["arrival_rate_rps"] != 1.4 or
            {name: flow["weight"] for name, flow in machine["workflows"].items()}
            != prereg["workload"]["workflow_weights"]):
        raise ValueError("Workload policy drift")
    return prereg, plan, expected, infrastructure, canary_metric_unknown


def monitor_row(gateway, previous_counters, interval_index, expected_relay,
                expected_business_uids):
    name = gateway["pod_name"]
    prefix = 'k8s_pod_name="' + name + '"'
    metrics = {
        "receiver_accepted_grpc": 'otelcol_receiver_accepted_spans_total{' + prefix + ',transport="grpc"}',
        "receiver_refused_grpc": 'otelcol_receiver_refused_spans_total{' + prefix + ',transport="grpc"}',
        "limiter_refused_spans": 'otelcol_processor_memory_limiter_refused_spans_total{' + prefix + '}',
        "jaeger_sent_spans": 'otelcol_exporter_sent_spans_total{' + prefix + ',exporter="otlp_grpc/jaeger"}',
        "jaeger_queue_size": 'otelcol_exporter_queue_size{' + prefix + ',exporter="otlp_grpc/jaeger"}',
        "heap_bytes": 'otelcol_process_runtime_heap_alloc_bytes{' + prefix + '}',
        "working_set_bytes": 'container_memory_working_set_bytes{namespace="otel-demo",pod="' + name + '",container="opentelemetry-collector"}',
        "cpu_cores": 'rate(container_cpu_usage_seconds_total{namespace="otel-demo",pod="' + name + '",container="opentelemetry-collector"}[1m])',
    }
    values = {key: metric(expression, optional=key == "limiter_refused_spans")
              for key, expression in metrics.items()}
    counters = {key: values[key]["value"] for key in
        ("receiver_accepted_grpc", "receiver_refused_grpc", "limiter_refused_spans", "jaeger_sent_spans")}
    if previous_counters:
        for key, value in counters.items():
            if key == "limiter_refused_spans":
                old = previous_counters[key]
                if value is None and old is None:
                    continue
                if value is None and old is not None:
                    raise RuntimeError("Collector refusal metric disappeared")
                if old is None and value is not None and value > 0:
                    raise RuntimeError("Collector refusal series appeared with positive count")
                if old is None:
                    continue
            if value < previous_counters[key]:
                raise RuntimeError("Collector counter reset: " + key)
        if counters["receiver_refused_grpc"] > previous_counters["receiver_refused_grpc"] or (
                counters["limiter_refused_spans"] is not None and
                previous_counters["limiter_refused_spans"] is not None and
                counters["limiter_refused_spans"] > previous_counters["limiter_refused_spans"]):
            raise RuntimeError("Collector refusal in frozen reliability window")
    extra = None
    if interval_index % 4 == 0:
        extra = verify_infrastructure(gateway["pod_name"], gateway["pod_uid"],
                                      expected_relay, expected_business_uids)
        if (extra["master_free_bytes"] < 45000000000 or any(
                free < 50000000000 for free in extra["workers_free_bytes"].values())):
            raise RuntimeError("Study storage budget breached")
    return {"observed_utc": datetime.now(timezone.utc).isoformat(),
            "collector_pod": name, "metrics": values, "infrastructure": extra}, counters


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--applied", type=Path, required=True)
    parser.add_argument("--health", type=Path, required=True)
    parser.add_argument("--residue", type=Path, required=True)
    parser.add_argument("--source-lock", type=Path, required=True)
    parser.add_argument("--canary-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or PROCESS_STATE.exists() or len(args.canary_id) != 2:
        raise ValueError("One new continuous window and two Canary IDs required")
    prereg, plan, gateway, initial, canary_metric_unknown = verify_sources(args.prereg, args.health,
        args.residue, args.source_lock, args.plan, args.applied, args.canary_id)
    args.output.mkdir(parents=True)
    start = datetime.now(timezone.utc) + timedelta(seconds=25)
    end = start + timedelta(seconds=2700)
    schedule = {"id": prereg["id"], "seed": prereg["seed"],
        "scheduled_start_utc": start.isoformat(), "scheduled_end_utc": end.isoformat(),
        "source_sha256": {str(path): sha(path) for path in
            (args.prereg, args.plan, args.applied, args.health, args.residue,
             args.source_lock, Path(__file__), STATE, CONTROLS, MACHINE, PROFILES, PEOPLE)},
        "collector_before": gateway, "initial_infrastructure": initial,
        "frontend_worker1_uid": initial["frontend_worker1_uid"],
        "canary_limiter_metric_unknown": canary_metric_unknown,
        "fault_injected": False, "formal_case": False}
    (args.output / "schedule.json").write_text(json.dumps(schedule, indent=2) + "\n")
    base = "http://10.104.177.128:8080"
    commands = {
        "state-machine": ["python3", "-B", str(STATE), "--machine", str(MACHINE),
            "--profiles", str(PROFILES), "--people", str(PEOPLE), "--profile", "MEDIUM",
            "--seed", str(prereg["seed"]), "--duration", "2700", "--start-utc",
            start.isoformat(), "--base-url", base, "--output", str(args.output / "state-machine")],
        "controls": ["python3", "-B", str(CONTROLS), "--plan", str(args.prereg),
            "--seed", str(prereg["seed"]), "--duration", "2700", "--start-utc",
            start.isoformat(), "--base-url", base, "--output", str(args.output / "controls")],
    }
    processes = {}
    handles = []
    timer_started = False
    stop_reason = None
    monitor_count = 0
    missing_limiter_metric_samples = 0
    try:
        for name, command in commands.items():
            stdout = (args.output / (name + ".stdout")).open("x")
            stderr = (args.output / (name + ".stderr")).open("x")
            handles.extend((stdout, stderr))
            processes[name] = subprocess.Popen(command, stdout=stdout, stderr=stderr)
        PROCESS_STATE.parent.mkdir(parents=True, exist_ok=True)
        PROCESS_STATE.write_text(json.dumps({"run_id": prereg["id"], "processes": {
            name: {"pid": process.pid} for name, process in processes.items()}}, indent=2) + "\n")
        watchdog = ["systemd-run", "--on-active=50m", "--unit=s5v3cr-continuous-cleanup",
            "/usr/bin/python3", "-B", str(ROOT / "scripts/cleanup_continuous.py"),
            "--state", str(PROCESS_STATE), "--output", str(ROOT / "results/continuous-watchdog-cleanup.json")]
        subprocess.run(watchdog, check=True, capture_output=True, text=True, timeout=15)
        timer_started = True
        previous = None
        pressure_count = 0
        with (args.output / "monitor.jsonl").open("x") as stream:
            planned = start.timestamp()
            while planned < end.timestamp():
                time.sleep(max(0, planned - time.time()))
                if time.time() - planned > 5:
                    stop_reason = "MONITOR_SCHEDULE_MISS"
                    break
                if any(process.poll() is not None and time.time() < end.timestamp() - 5
                       for process in processes.values()):
                    stop_reason = "WORKLOAD_PROCESS_EXITED_EARLY"
                    break
                try:
                    row, current = monitor_row(gateway, previous, monitor_count,
                                               plan["new_relay_sha256"], initial[
                                                   "business_pod_uids"])
                    pressure_count = pressure_count + 1 if row["metrics"][
                        "working_set_bytes"]["value"] >= 380 * 1048576 else 0
                    row["planned_utc"] = datetime.fromtimestamp(planned, timezone.utc).isoformat()
                    missing_limiter_metric_samples += row["metrics"][
                        "limiter_refused_spans"]["status"] == "SERIES_ABSENT_UNKNOWN_NOT_ZERO"
                    row["working_set_over_380_mib_consecutive"] = pressure_count
                    stream.write(json.dumps(row) + "\n")
                    stream.flush()
                    if pressure_count >= 2:
                        stop_reason = "COLLECTOR_MEMORY_PRESSURE_STOP"
                        break
                    previous = current
                except Exception as error:
                    stream.write(json.dumps({"planned_utc": datetime.fromtimestamp(
                        planned, timezone.utc).isoformat(), "error": str(error)}) + "\n")
                    stream.flush()
                    stop_reason = "MONITOR_FAIL_CLOSED: " + str(error)
                    break
                monitor_count += 1
                planned = start.timestamp() + monitor_count * prereg["monitor_interval_seconds"]
        if stop_reason is None:
            codes = {name: process.wait(timeout=120) for name, process in processes.items()}
            manifests = {name: read(args.output / name / "result-manifest.json")
                         for name in commands}
            if any(code != 0 for code in codes.values()) or any(
                    row["status"] not in ("LOAD_PASS", "CONTROL_STREAMS_COMPLETE")
                    for row in manifests.values()):
                stop_reason = "WORKLOAD_MANIFEST_OR_EXIT_FAILURE"
        result = {"status": ("CONTINUOUS_DIAGNOSTIC_WORKLOAD_COMPLETED" if
                  canary_metric_unknown or missing_limiter_metric_samples else
                  "CONTINUOUS_WORKLOAD_COMPLETED") if not stop_reason else
                  "CONTINUOUS_WORKLOAD_FAILED", "stop_reason": stop_reason,
                  "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                  "monitor_samples": monitor_count,
                  "limiter_metric_missing_samples": missing_limiter_metric_samples,
                  "scheduled_monitor_samples": 180,
                  "schedule_sha256": sha(args.output / "schedule.json"),
                  "monitor_sha256": sha(args.output / "monitor.jsonl"),
                  "fault_injected": False, "formal_case": False}
        (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result), flush=True)
        if stop_reason:
            raise SystemExit(1)
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
        for handle in handles:
            handle.close()
        if timer_started:
            subprocess.run(["systemctl", "stop", "s5v3cr-continuous-cleanup.timer"],
                           capture_output=True, text=True, timeout=15)


if __name__ == "__main__":
    main()
