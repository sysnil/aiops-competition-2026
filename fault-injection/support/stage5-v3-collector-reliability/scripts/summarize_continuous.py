#!/usr/bin/env python3
"""Apply preregistered reliability states to the immutable 45-minute evidence."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import yaml


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--late", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--archive-proof", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or len(args.archive_proof) != 2:
        raise ValueError("New output and dual Worker proofs required")
    policy = yaml.safe_load(args.prereg.read_text())
    schedule = read(args.run / "schedule.json")
    workload = read(args.run / "result.json")
    first, late, comparison = [read(path) for path in
        (args.first, args.late, args.comparison)]
    proofs = [read(path) for path in args.archive_proof]
    if (policy["id"] != schedule["id"] or
            {row["worker_ip"] for row in proofs} != {"192.168.53.54", "192.168.53.55"}
            or any(row["status"] != "CONTINUOUS_ARCHIVE_VERIFIED_AND_RESTORED"
                   for row in proofs)):
        raise ValueError("Pre-registered study or dual raw archive missing")
    monitor = [json.loads(line) for line in (args.run / "monitor.jsonl").read_text().splitlines()
               if line.strip()]
    complete_rows = [row for row in monitor if "metrics" in row]
    errors = [row for row in monitor if "error" in row]
    metrics = {}
    for name in ("receiver_accepted_grpc", "receiver_refused_grpc", "limiter_refused_spans",
                 "jaeger_sent_spans", "jaeger_queue_size", "heap_bytes",
                 "working_set_bytes", "cpu_cores"):
        values = [row["metrics"][name]["value"] for row in complete_rows
                  if row["metrics"][name]["value"] is not None]
        statuses = [row["metrics"][name]["status"] for row in complete_rows]
        metrics[name] = {"observed_values": len(values),
            "series_absent_samples": statuses.count("SERIES_ABSENT_UNKNOWN_NOT_ZERO"),
            "first": values[0] if values else None, "last": values[-1] if values else None,
            "observed_counter_difference": values[-1] - values[0] if values and name in (
                "receiver_accepted_grpc", "receiver_refused_grpc", "limiter_refused_spans",
                "jaeger_sent_spans") else None,
            "min": min(values) if values else None, "max": max(values) if values else None}
    summary = first["summary"]
    uid = summary["uid_counts"]
    uid_complete = all(uid.get(service, {}).get("server_spans", 0) >= 30 and
        uid[service].get("direct_uid_verified") == uid[service]["server_spans"]
        and uid[service].get("missing_uid", 0) == 0 and
        uid[service].get("mismatched_uid", 0) == 0
        for service in ("cart", "checkout", "shipping", "quote"))
    trace_complete = (summary["issued_requests"] > 0 and summary["proxy_server_matched"] ==
        summary["issued_requests"] and not summary["missing_proxy_trace_ids"] and
        all(not values for values in summary["missing_control_backend"].values()) and
        not summary["missing_cart_frontend"] and uid_complete and
        summary["failed_request_rows"] == 0 and not summary["request_window_errors"])
    archive_complete = all(row["source_manifest_sha256"] == proofs[0][
        "source_manifest_sha256"] for row in proofs)
    infrastructure_complete = (workload["status"] in ("CONTINUOUS_WORKLOAD_COMPLETED",
        "CONTINUOUS_DIAGNOSTIC_WORKLOAD_COMPLETED") and len(monitor) == 180 and
        not errors and archive_complete and first["status"] == late["status"] ==
        "TRACE_CAPTURE_COMPLETE" and comparison["status"] ==
        "CONTINUOUS_TRACE_COMPLETION_PASS")
    grpc_delta = metrics["receiver_refused_grpc"]["observed_counter_difference"]
    grpc_no_refusal = grpc_delta == 0
    limiter = metrics["limiter_refused_spans"]
    limiter_unknown = limiter["series_absent_samples"] > 0
    if not trace_complete or (grpc_delta is not None and grpc_delta > 0):
        status = "RELIABILITY_FAIL"
    elif not infrastructure_complete or grpc_delta is None:
        status = "RELIABILITY_INCONCLUSIVE"
    elif limiter_unknown or metrics["limiter_refused_spans"]["observed_counter_difference"] is None:
        status = "RELIABILITY_INCONCLUSIVE"
    elif metrics["limiter_refused_spans"]["observed_counter_difference"] == 0:
        status = "RELIABILITY_PASS_OBSERVED_WINDOW"
    else:
        status = "RELIABILITY_FAIL"
    value = {"status": status, "at_utc": datetime.now(timezone.utc).isoformat(),
        "scheduled_start_utc": schedule["scheduled_start_utc"],
        "scheduled_end_utc": schedule["scheduled_end_utc"],
        "duration_seconds": policy["workload"]["duration_seconds"],
        "monitor_planned_samples": 180, "monitor_actual_rows": len(monitor),
        "monitor_error_count": len(errors), "workload_status": workload["status"],
        "metrics": metrics, "limiter_counter_absent_not_zero": limiter_unknown,
        "requests_and_traces": summary,
        "trace_completion": comparison,
        "dual_worker_archive_verified": archive_complete,
        "trace_evidence_complete": trace_complete,
        "infrastructure_observation_complete": infrastructure_complete,
        "grpc_receiver_refused_zero_observed_delta": grpc_no_refusal,
        "historical_32_span_cause_identified_by_this_window": False,
        "formal_clean_gate_approved": False,
        "source_sha256": {str(path): sha(path) for path in (args.prereg,
            args.run / "schedule.json", args.run / "result.json", args.run / "monitor.jsonl",
            args.first, args.late, args.comparison, *args.archive_proof)},
        "private_gt_read": False, "fault_injected": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": status, "issued": summary["issued_requests"],
        "proxy_matched": summary["proxy_server_matched"],
        "monitor_samples": len(monitor), "limiter_unknown": limiter_unknown}))


if __name__ == "__main__":
    main()
