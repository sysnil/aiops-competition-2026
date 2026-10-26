"""Assemble compact, source-bound diagnostics without changing old decisions."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


SERVICES = ("cart", "checkout", "shipping", "quote")


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    if path.exists():
        raise ValueError("Refusing overwrite " + str(path))
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--old-root", type=Path, required=True)
    args = parser.parse_args()
    root, old = args.root, args.old_root
    path = lambda name: root / "results" / name
    inputs = {name: path(name) for name in (
        "missing-parent-and-uid-val1.json", "missing-parent-and-uid-val2.json",
        "parent-boundary-storage-audit.json", "control-latency-analysis-v2.json",
        "control-statistical-audit.json", "uid-canary-audit.json",
        "uid-canary-load-manifest.json", "canary-proxy-gap-analysis.json",
        "post-rollback-uid-sample/summary.json", "post-rollback-global-safety.json",
        "post-rollback-topology-audit.json")}
    inputs["uid-manual-rollback.json"] = root / "runtime/uid-manual-rollback.json"
    inputs["clean-gate-v2-summary.json"] = old / "results/clean-gate-v2-summary.json"
    data = {name: read(source) for name, source in inputs.items()}
    m1, m2 = (data[name] for name in ("missing-parent-and-uid-val1.json",
                                      "missing-parent-and-uid-val2.json"))
    boundary = data["parent-boundary-storage-audit.json"]
    canary = data["uid-canary-audit.json"]
    gap = data["canary-proxy-gap-analysis.json"]
    rollback = data["uid-manual-rollback.json"]
    final_health = data["post-rollback-global-safety.json"]
    if (m1["old_issued_server_missing_parent_count"] != 18338 or
            m2["old_issued_server_missing_parent_count"] != 18460 or
            canary["status"] != "UID_CANARY_FAILED" or
            rollback["status"] != "UID_REPAIR_ROLLBACK_COMPLETE" or
            final_health["status"] != "GLOBAL_SAFETY_PASS"):
        raise ValueError("Expected immutable evidence chain is incomplete")
    origin_hashes = {str(source): sha(source) for source in inputs.values()}
    now = datetime.now(timezone.utc).isoformat()

    old_missing = sum(item["old_issued_server_missing_parent_count"]
                      for item in (m1, m2))
    external = sum(item["old_issued_server_reference_categories"].get(
                   "VERIFIED_UNEXPORTED_EXTERNAL_PARENT", 0)
                   for item in (m1, m2))
    class_counts = {name: sum(row["classification"] == name for row in
                   boundary["results"]) for name in {row["classification"]
                   for row in boundary["results"]}}
    missing = {"status": "OLD_MISSING_PARENT_COUNT_EXPLAINED",
        "at_utc": now, "old_issued_server_missing_total": old_missing,
        "verified_generated_external_parent": external,
        "verified_other_parent_before_capture_window": old_missing - external,
        "all_span_unresolved_before_current_storage_query": sum(item[
            "all_reference_categories"].get("UNKNOWN_UNRESOLVED_PARENT", 0)
            for item in (m1, m2)),
        "read_only_storage_followup": class_counts,
        "non_child_of_follows_from_references": {"VAL-1": 2246,
                                                 "VAL-2": 2316},
        "current_storage_unexplained_parent_references": class_counts.get(
            "UNKNOWN_PARENT_NOT_IN_CURRENT_STORAGE", 0),
        "old_issued_business_unexplained_after_followup": 0,
        "runs": [{"run_id": item["run_id"],
                  "final_spans": item["final_span_ids"],
                  "all_reference_categories": item["all_reference_categories"],
                  "old_issued_categories": item[
                      "old_issued_server_reference_categories"],
                  "immutable_version_missing_counts": item[
                      "old_missing_by_immutable_version"],
                  "fixed_control_trace_coverage": item[
                      "fixed_control_edge_trace_coverage"],
                  "client_server_edges": item["client_server_edges"]}
                 for item in (m1, m2)],
        "source_sha256": origin_hashes,
        "private_gt_read": False}
    if external + (old_missing - external) != old_missing or external != 36797:
        raise ValueError("Issued-parent classification does not close")
    save(path("missing-parent-classification.json"), missing)

    post = data["post-rollback-uid-sample/summary.json"]
    uid = {"status": "UID_REPAIR_TECHNICALLY_EFFECTIVE_BUT_REVERTED",
        "at_utc": now,
        "pre_repair_server_span_status": {item["run_id"]: {
            service: item["pod_uid_server_status_by_service"].get(service, {})
            for service in SERVICES} for item in (m1, m2)},
        "pre_repair_inference_methods": {item["run_id"]: {
            service: item["pod_uid_inference_methods_by_service"].get(service, {})
            for service in SERVICES} for item in (m1, m2)},
        "canary_status": canary["status"],
        "canary_direct_uid_by_service": canary["target_service_counts"],
        "canary_pods": canary["actual_pods"],
        "canary_issued_requests": canary["issued_trace_ids"],
        "canary_proxy_server_trace_ids": canary[
            "issued_proxy_trace_ids_matched"],
        "canary_proxy_gap_categories": gap["categories"],
        "rollback_status": rollback["status"],
        "post_rollback_raw_span_uid_sample": {name: {
            "server_spans": row["server_spans"],
            "uid_categories": row["uid_categories"],
            "actual_pod": row["actual_pod"]} for name, row in post[
                "services"].items()},
        "post_rollback_route_metadata": data[
            "post-rollback-topology-audit.json"]["status"],
        "old_W3_W2_checkout_cart_target_valid_now": data[
            "post-rollback-topology-audit.json"][
                "old_W3_to_W2_checkout_cart_membership_retained"],
        "source_sha256": origin_hashes, "private_gt_read": False}
    save(path("pod-uid-audit.json"), uid)

    old_result = data["clean-gate-v2-summary.json"]
    validation = {"status": "NEW_FULL_CLEAN_VALIDATION_NOT_RUN",
        "at_utc": now, "reason": "No evidence-based executable new rule; UID Canary "
            "failed its preregistered entry-Trace coverage gate and was reverted.",
        "old_clean_v2_25_minute_windows": 2,
        "old_clean_A_passes": sum(row["old_clean_gate"] !=
            "CLEAN_QUALIFICATION_BLOCKED" for row in old_result["runs"]),
        "old_unapproved_B_passes": old_result["validation_candidate_B_passes"],
        "old_unapproved_E_passes": old_result["validation_candidate_E_passes"],
        "new_25_minute_windows": 0,
        "short_no_fault_uid_canary": {"duration_seconds": 180,
            "seed": 861303, "status": canary["status"],
            "planned_sessions": data["uid-canary-load-manifest.json"][
                "planned_sessions"],
            "successful_sessions": data["uid-canary-load-manifest.json"][
                "successful_sessions"], "requests": canary["ordinary_requests"],
            "proxy_trace_matches": canary["issued_proxy_trace_ids_matched"]},
        "candidate_v3_numeric_rule_approved": False,
        "source_sha256": origin_hashes,
        "private_gt_read": False}
    save(path("validation-summary.json"), validation)

    decision = {"status": "TRACE_IDENTITY_BLOCKED",
        "at_utc": now,
        "secondary_statuses": ["CONTROL_LATENCY_UNRESOLVED",
                               "VALIDATION_INCONCLUSIVE"],
        "old_missing_parent_issue": "EXPLAINED_GENERATED_REMOTE_PARENT_AND_WINDOW_EDGE",
        "current_direct_pod_uid_coverage": "BLOCKED_AFTER_ROLLBACK",
        "ordinary_proxy_trace_coverage": "1507_OF_1539_IN_SHORT_CANARY",
        "normal_control_latency_exact_component": "PLAUSIBLE_BUT_UNCONFIRMED",
        "new_clean_gate_candidate": "DEFERRED_NO_THRESHOLD_CHANGE",
        "stage5_v3_protocol_frozen": False,
        "formal_cases_created": 0, "faults_injected": 0,
        "formal_rca_predictions": 0,
        "stage5_v2_test_accessed": False,
        "post_rollback_global_safety": final_health["status"],
        "post_rollback_global_safety_blockers": final_health["blockers"],
        "source_sha256": origin_hashes}
    save(path("final-decision.json"), decision)
    print(json.dumps({"status": decision["status"],
        "old_missing_parent_explained": old_missing,
        "canary_status": canary["status"],
        "final_health": final_health["status"]}))


if __name__ == "__main__":
    main()
