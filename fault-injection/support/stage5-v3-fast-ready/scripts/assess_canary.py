#!/usr/bin/env python3
"""Apply the preregistered no-fault Canary quality and archive checks."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import yaml


TARGETS = ("cart", "checkout", "shipping", "quote")


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--reference-policy", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--pre-health", type=Path, required=True)
    parser.add_argument("--post-health", type=Path, required=True)
    parser.add_argument("--pre-residue", type=Path, required=True)
    parser.add_argument("--post-residue", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--archive-proof", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or len(args.archive_proof) != 2:
        raise ValueError("New output and two Worker proofs required")
    plan = yaml.safe_load(args.prereg.read_text())
    from data_disk_guard import assert_ready
    disk = assert_ready(args.run)
    if plan.get("disk", {}).get("uuid") != disk["uuid"] or not args.run.is_relative_to(
            Path("/data/stage5-v3/fast-ready/runs")):
        raise ValueError("Scored run is not on the registered data disk")
    schedule = read(args.run / "schedule.json")
    workload = read(args.run / "result.json")
    trace = read(args.run / "trace/trace-schedule.json")
    scoring = read(args.run / "trace/scoring_plus120/stage-manifest.json")
    audit = read(args.run / "trace/audit_plus240/stage-manifest.json")
    before, after = read(args.pre_health), read(args.post_health)
    residues = (read(args.pre_residue), read(args.post_residue))
    proofs = [read(path) for path in args.archive_proof]
    quality = plan["quality"]
    inherited = yaml.safe_load(args.reference_policy.read_text())["quality_gate"]
    if (inherited["uid_issued_server_spans_each_target_min"] != 30 or
            inherited["collector_limiter_refused_spans_delta_max"] != 0 or
            inherited["collector_grpc_refused_spans_delta_max"] != 0):
        raise ValueError("Existing pre-run quality reference changed")
    counts = scoring["summary"]["uid_counts"]
    counters = {}
    for name in ("receiver_refused_grpc", "receiver_refused_http", "limiter_refused"):
        first = schedule["collector_counters_before"][name]
        last = workload["collector_counters_after"][name]
        same_pod = first.get("labels", {}).get("k8s_pod_name") == last.get(
            "labels", {}).get("k8s_pod_name") == schedule["gateway_pod"]
        delta = last["value"] - first["value"] if all(row["status"] == "OBSERVABLE"
            for row in (first, last)) and same_pod else None
        counters[name] = {"before": first.get("value"), "after": last.get("value"),
                          "observed_delta": delta,
                          "status": "OBSERVABLE" if delta is not None else "UNKNOWN_NOT_ZERO"}
    summary = scoring["summary"]
    checks = {
        "preregistration_and_source": plan["status"] == "PREREGISTERED_NO_FAULT_CANARY"
            and schedule["id"] == plan["id"] and not schedule["fault_injected"] and
            schedule["source_sha256"][str(args.prereg)] == sha(args.prereg),
        "workload_complete": workload["status"] == "CANARY_WORKLOAD_COMPLETE" and
            workload["exit_codes"] == {"state-machine": 0, "controls": 0},
        "client_requests_successful": workload["manifests"]["state-machine"][
            "request_success_fraction"] == 1 and all(row["successful_requests"] ==
            row["planned_requests"] for row in workload["manifests"]["controls"][
                "streams"].values()),
        "timed_trace": trace["status"] == "TIMED_TRACE_CAPTURE_READY" and
            set(trace["stages"]) == {"first_plus5", "supplement_plus60",
                "scoring_plus120", "audit_plus240"},
        "proxy_server_coverage": summary["issued_trace_ids"] > 0 and
            summary["proxy_server_matched"] / summary["issued_trace_ids"] >=
            quality["proxy_server_coverage_fraction_min"] and not summary["missing_proxy"],
        "fixed_business_backends": not summary["fixed_image_backend_missing"] and
            not summary["fixed_cart_backend_missing"] and
            not summary["fixed_cart_frontend_missing"],
        "four_service_direct_uid": all(counts[name]["server_spans"] >=
            inherited["uid_issued_server_spans_each_target_min"] and
            counts[name]["direct_uid_verified"] == counts[name]["server_spans"] and
            not counts[name]["missing_uid"] and not counts[name]["mismatched_uid"]
            for name in TARGETS),
        "no_late_after_scoring": trace["late_after_scoring_count"] == 0 and
            audit["new_span_identities"] == 0,
        "receiver_refused_observed_zero": all(counters[name]["observed_delta"] ==
            inherited["collector_grpc_refused_spans_delta_max"] for name in (
                "receiver_refused_grpc", "receiver_refused_http")),
        "limiter_refused_observed_zero": counters["limiter_refused"][
            "observed_delta"] == inherited["collector_limiter_refused_spans_delta_max"],
        "pod_identity_stable": workload["pod_uids_unchanged"],
        "pre_post_global_safety": before["status"] == after["status"] ==
            "GLOBAL_SAFETY_PASS",
        "five_host_residue": all(row["status"] == "HOST_RESIDUE_PASS"
                                 for row in residues),
        "dual_archive": {row["status"] for row in proofs} ==
            {"CANARY_ARCHIVE_VERIFIED_AND_RESTORED"} and
            {row["worker_ip"] for row in proofs} == {"192.168.53.54",
                                                  "192.168.53.55"} and
            len({row["source_manifest_sha256"] for row in proofs}) == 1 and
            proofs[0]["source_manifest_sha256"] == sha(args.source_manifest),
    }
    value = {"status": "OBSERVABILITY_SMOKE_PASS" if all(checks.values()) else
        "OBSERVABILITY_SMOKE_FAIL", "at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": plan["id"], "window": [schedule["scheduled_start_utc"],
            schedule["scheduled_end_utc"]], "checks": checks,
        "blockers": [name for name, passed in checks.items() if not passed],
        "collector_counter_deltas": counters,
        "issued_requests": summary["issued_trace_ids"],
        "proxy_server_matched": summary["proxy_server_matched"],
        "direct_uid_counts": counts,
        "trace_stage_offsets": {name: {key: item[key] for key in (
            "start_lag_seconds", "completion_lag_seconds", "unique_spans")}
            for name, item in trace["stages"].items()},
        "late_after_scoring_count": trace["late_after_scoring_count"],
        "archive_manifest_sha256": sha(args.source_manifest),
        "data_disk_guard": disk,
        "existing_quality_reference_sha256": sha(args.reference_policy),
        "source_sha256": {str(path): sha(path) for path in (
            args.prereg, args.reference_policy, args.run / "schedule.json", args.run / "result.json",
            args.run / "trace/trace-schedule.json", args.pre_health,
            args.post_health, args.pre_residue, args.post_residue,
            args.source_manifest, *args.archive_proof)},
        "long_term_reliability_proven": False, "formal_case": False,
        "fault_injected": False}
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "blockers": value["blockers"],
                      "issued": value["issued_requests"]}))
    if value["status"] != "OBSERVABILITY_SMOKE_PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
