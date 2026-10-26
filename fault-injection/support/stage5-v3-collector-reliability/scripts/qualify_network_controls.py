#!/usr/bin/env python3
"""Apply preregistered no-fault stability and route checks to current controls."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics

import yaml


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def one_pod(topology, service, node):
    rows = [item for item in topology["service_pods"].get(service, []) if item[
        "node"] == node and item["ready"]]
    return rows[0] if len(rows) == 1 else None


def pod_by_ip(topology, service, ip):
    rows = [item for item in topology["service_pods"].get(service, []) if item[
        "ip"] == ip and item["ready"]]
    return rows[0] if len(rows) == 1 else None


def stability(minutes, name, request_key, edge_key, p95_key, rules):
    reference = [row[p95_key]["p95"] for row in minutes[:10] if row[p95_key]["p95"] is not None]
    baseline = statistics.median(reference) if len(reference) == 10 else None
    rows = []
    for item in minutes:
        p95 = item[p95_key]["p95"]
        ratio = p95 / baseline if p95 is not None and baseline and baseline > 0 else None
        counts_ok = item[request_key] >= rules.get("minimum_successful_requests_per_minute",
            rules.get("minimum_valid_client_server_edges_per_minute", 30)) and item[
            edge_key] >= rules["minimum_valid_client_server_edges_per_minute"]
        latency_ok = ratio is not None and ratio <= rules[
            "p95_ratio_against_first_ten_minute_median_max"]
        rows.append({"minute": item["minute"], "requests_or_client_spans": item[request_key],
            "valid_edge_spans": item[edge_key], "p95_ms": p95,
            "p95_absolute_change_ms": p95 - baseline if p95 is not None and baseline else None,
            "p95_ratio": ratio, "count_pass": counts_ok, "latency_pass": latency_ok})
    return {"control": name, "baseline_first_ten_minute_median_p95_ms": baseline,
        "all_45_minutes_count_pass": all(row["count_pass"] for row in rows),
        "all_45_minutes_latency_pass": all(row["latency_pass"] for row in rows),
        "count_failure_minutes": [row["minute"] for row in rows if not row["count_pass"]],
        "latency_failure_minutes": [row["minute"] for row in rows if not row["latency_pass"]],
        "minute_rows": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--topology-before", type=Path, required=True)
    parser.add_argument("--topology-after", type=Path, required=True)
    parser.add_argument("--route-scope", type=Path, required=True)
    parser.add_argument("--target-packets", type=Path, required=True)
    parser.add_argument("--recommendation-packets", type=Path, required=True)
    parser.add_argument("--continuous-capture", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    policy = yaml.safe_load(args.prereg.read_text())
    if policy["status"] != "PREREGISTERED_BEFORE_CONTINUOUS_WINDOW":
        raise ValueError("Control rules not preregistered")
    before, after = read(args.topology_before), read(args.topology_after)
    route, target, recommendation, capture = [read(path) for path in
        (args.route_scope, args.target_packets, args.recommendation_packets,
         args.continuous_capture)]
    minutes = capture["summary"]["per_minute"]
    if len(minutes) != 45:
        raise ValueError("Full 45-minute control window absent")
    image = stability(minutes, "image", "image_success", "image_edge_spans",
        "image_client_latency_ms", policy["candidate_controls"]["image"])
    rec = stability(minutes, "recommendation", "w1_recommendation_client_spans",
        "w1_recommendation_client_spans", "w1_recommendation_client_latency_ms",
        policy["candidate_controls"]["recommendation"])
    checkout_before = one_pod(before, "checkout", "k8s-worker2")
    checkout_after = one_pod(after, "checkout", "k8s-worker2")
    cart_before = one_pod(before, "cart", "k8s-worker3")
    cart_after = one_pod(after, "cart", "k8s-worker3")
    target_current = bool(checkout_before and checkout_after and cart_before and cart_after
        and checkout_before["uid"] == checkout_after["uid"] and
        cart_before["uid"] == cart_after["uid"] and
        target["status"] == "BUSINESS_PACKET_PROOF_PRESENT" and
        target["source_pod_ip"] == checkout_after["ip"] and
        target["destination_pod_ip"] == cart_after["ip"] and
        route.get("w2_to_cart_via_worker3") and route.get("w2_image_local"))
    frontend_before = one_pod(before, "frontend", "k8s-worker1")
    frontend_after = one_pod(after, "frontend", "k8s-worker1")
    rec_before = pod_by_ip(before, "recommendation", recommendation["destination_pod_ip"])
    rec_after = pod_by_ip(after, "recommendation", recommendation["destination_pod_ip"])
    rec_current = bool(frontend_before and frontend_after and rec_before and rec_after
        and frontend_before["uid"] == frontend_after["uid"] and
        rec_before["uid"] == rec_after["uid"] and
        recommendation["status"] == "BUSINESS_PACKET_PROOF_PRESENT" and
        recommendation["source_pod_ip"] == frontend_after["ip"] and
        recommendation["destination_pod_ip"] == rec_after["ip"] and
        route.get("w1_to_recommendation_via_worker3"))
    adapter_text = args.adapter.read_text()
    adapter_current_supported = ("NETWORK_PATH/k8s-worker2/k8s-worker3" in adapter_text
        and "socket.gethostname()" in adapter_text and
        "tc', 'filter'" in adapter_text)
    image_status = ("QUALIFIED" if target_current and image[
        "all_45_minutes_count_pass"] and image["all_45_minutes_latency_pass"] and
        adapter_current_supported else "CONDITIONAL" if target_current else "NOT_QUALIFIED")
    rec_status = ("CONDITIONAL" if rec_current and rec[
        "all_45_minutes_count_pass"] and rec["all_45_minutes_latency_pass"] else
        "NOT_QUALIFIED")
    matrix = {
        "SERVICE/recommendation": {"image": image_status,
            "cart": "CONDITIONAL_SHARED_FRONTEND_PROPAGATION"},
        "POD/recommendation": {"image": image_status,
            "cart": "CONDITIONAL_SHARED_FRONTEND_PROPAGATION"},
        "NODE/k8s-worker1": {"image": image_status,
            "cart": "CONDITIONAL_ONLY_UID_VERIFIED_FRONTEND_WORKER2_SUBSET"},
        "NETWORK_PATH/k8s-worker2/k8s-worker3": {
            "image": image_status, "frontend_worker1_to_recommendation_worker3": rec_status,
            "cart": "NOT_QUALIFIED_TARGET_PATH_OVERLAP"}}
    value = {"status": "SECOND_CONTROL_NOT_QUALIFIED" if rec_status != "QUALIFIED"
        else "TWO_CONTROLS_QUALIFIED", "at_utc": datetime.now(timezone.utc).isoformat(),
        "target_path_current_proof": target_current,
        "image_control": image, "recommendation_control": rec,
        "image_status": image_status, "recommendation_status": rec_status,
        "adapter_supports_current_w2_w3_path": adapter_current_supported,
        "adapter_sha256": sha(args.adapter),
        "adapter_note": "Existing code is hardcoded for W3->W2; W2->W3 egress filter and shared Worker3 risk need separate preregistered rehearsal",
        "w1_to_w3_route_distinct_from_w2_to_w3": rec_current,
        "shared_worker3_target_resource_risk": True,
        "probe_requests_counted_as_business": False,
        "mechanism_control_matrix": matrix,
        "alternative_unverified_business_controls": [
            "frontend_worker1_to_product-catalog_worker3",
            "checkout_worker2_to_payment_worker1_or_worker2_must_resolve_actual_pod"],
        "source_sha256": {str(path): sha(path) for path in (args.prereg,
            args.topology_before, args.topology_after, args.route_scope,
            args.target_packets, args.recommendation_packets, args.continuous_capture)},
        "fault_injected": False, "private_gt_read": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "image": image_status,
                      "recommendation": rec_status, "adapter_current_supported": adapter_current_supported}))


if __name__ == "__main__":
    main()
