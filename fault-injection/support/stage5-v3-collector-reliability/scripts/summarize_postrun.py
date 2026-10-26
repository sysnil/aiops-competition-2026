#!/usr/bin/env python3
"""Summarize immutable monitoring without treating missing series as zero."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


METRICS = (
    "receiver_accepted_grpc", "receiver_refused_grpc", "limiter_refused_spans",
    "jaeger_sent_spans", "jaeger_queue_size", "heap_bytes",
    "working_set_bytes", "cpu_cores",
)
COUNTERS = {"receiver_accepted_grpc", "receiver_refused_grpc",
            "limiter_refused_spans", "jaeger_sent_spans"}


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def summarize(run):
    rows = [json.loads(line) for line in (run / "monitor.jsonl").read_text().splitlines()
            if line.strip()]
    schedule = json.loads((run / "schedule.json").read_text())
    result = json.loads((run / "result.json").read_text())
    state = json.loads((run / "state-machine/result-manifest.json").read_text())
    controls = json.loads((run / "controls/result-manifest.json").read_text())
    errors = [row for row in rows if row.get("error")]
    names = sorted({row.get("collector_pod") for row in rows})
    metrics = {}
    for name in METRICS:
        samples = [row.get("metrics", {}).get(name, {}) for row in rows]
        values = [item["value"] for item in samples if item.get("value") is not None]
        instances = sorted({item.get("labels", {}).get("service_instance_id") for item in samples
                            if item.get("labels", {}).get("service_instance_id")})
        resets = sum(after < before for before, after in zip(values, values[1:])) \
            if name in COUNTERS else None
        metrics[name] = {"observed_samples": len(values),
                         "missing_samples": len(samples) - len(values),
                         "statuses": sorted({item.get("status") for item in samples}),
                         "collector_instances": instances,
                         "first": values[0] if values else None,
                         "last": values[-1] if values else None,
                         "minimum": min(values) if values else None,
                         "maximum": max(values) if values else None,
                         "counter_resets": resets,
                         "observed_counter_delta": values[-1] - values[0]
                         if name in COUNTERS and len(values) == len(rows) and not resets
                         else None}
    infra = [row["infrastructure"] for row in rows if row.get("infrastructure")]
    uid_keys = ("cart", "checkout", "shipping", "quote", "frontend", "recommendation")
    uid_sets = {name: sorted({tuple(item["business_pod_uids"].get(name, [])) for item in infra})
                for name in uid_keys}
    node_failures = [index for index, item in enumerate(infra) if any(
        state.get("Ready") != "True" or state.get("DiskPressure") != "False"
        for state in item["nodes"].values())]
    return {"status": "MONITOR_COMPLETE_TRACE_TIMELINESS_UNVERIFIED"
            if len(rows) == 180 and not errors else "MONITOR_INCOMPLETE_OR_ERROR",
            "at_utc": datetime.now(timezone.utc).isoformat(),
            "run_id": schedule["id"],
            "scheduled_start_utc": schedule["scheduled_start_utc"],
            "scheduled_end_utc": schedule["scheduled_end_utc"],
            "monitor_actual_rows": len(rows), "monitor_planned_rows": 180,
            "monitor_error_count": len(errors), "collector_pods": names,
            "metrics": metrics, "infrastructure_samples": len(infra),
            "business_pod_uid_sets": uid_sets,
            "node_ready_or_disk_pressure_failure_sample_indexes": node_failures,
            "opensearch_states": sorted({item.get("opensearch") for item in infra}),
            "minimum_master_free_bytes": min((item["master_free_bytes"] for item in infra),
                                              default=None),
            "minimum_worker_free_bytes": {name: min((item["workers_free_bytes"][name]
                for item in infra), default=None) for name in ("worker2", "worker3")},
            "workload_result": result,
            "state_machine_result": state,
            "fixed_controls_result": controls,
            "source_sha256": {name: sha(run / name) for name in (
                "schedule.json", "monitor.jsonl", "result.json",
                "state-machine/result-manifest.json", "controls/result-manifest.json")},
            "trace_capture_plus_120_complete": False,
            "trace_capture_plus_240_complete": False,
            "fault_injected": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    result = summarize(args.run)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"],
        "monitor_rows": result["monitor_actual_rows"],
        "receiver_refused_delta": result["metrics"]["receiver_refused_grpc"][
            "observed_counter_delta"],
        "limiter_missing": result["metrics"]["limiter_refused_spans"]["missing_samples"]}))


if __name__ == "__main__":
    main()
