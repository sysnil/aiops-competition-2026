#!/usr/bin/env python3
"""Separate four-layer engineering readiness from formal fault authorization."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path("/root/stage5-v3-fast-ready")
SOURCES = {
    "topology": ROOT / "snapshots/current-topology-20261013/topology.json",
    "distribution": ROOT / "results/business-distribution.json",
    "cgroup": ROOT / "results/recommendation-cgroup.json",
    "worker1": ROOT / "results/worker1-resources.json",
    "network": ROOT / "results/network-path-dry-run.json",
    "control": ROOT / "results/payment-control-exploratory.json",
    "smoke": ROOT / "results/observability-smoke.json",
    "safety": ROOT / "results/postcanary-global-safety.json",
    "clean": ROOT / "results/clean-gate.json",
    "collector": ROOT / "results/collector-metrics.json",
}
OUT = ROOT / "results/readiness-by-layer.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUT.exists():
        raise ValueError("Refusing overwrite")
    data = {name: json.loads(path.read_text()) for name, path in SOURCES.items()}
    pods = data["topology"]["service_pods"]
    recommendation = [item for item in pods["recommendation"] if item["ready"]]
    same_uids = {item["uid"] for item in recommendation} == set(data[
        "distribution"]["per_pod_server_spans_by_minute"]["recommendation"])
    two_pods_covered = (len(recommendation) == 2 and same_uids and data[
        "distribution"]["recommendation_each_replica_30_per_minute"])
    cgroup = data["cgroup"]["status"] == "RECOMMENDATION_CGROUP_PREFLIGHT_PASS"
    safe = data["safety"]["status"] == "GLOBAL_SAFETY_PASS"
    smoke = data["smoke"]["status"] == "OBSERVABILITY_SMOKE_PASS"
    service = {"layer": "SERVICE", "target": "recommendation",
        "status": "CONDITIONAL" if two_pods_covered and safe and smoke else "BLOCKED",
        "evidence": {"two_ready_replicas": len(recommendation) == 2,
            "each_replica_30_server_spans_each_canary_minute": two_pods_covered,
            "process_stall_adapter_staged_read_only": True,
            "direct_uid_trace_available": same_uids},
        "blockers": ["Independent non-target normal service control has not passed the existing full-window stability rule",
            "Service-wide direct signal and recovery need two bounded fault rehearsals",
            "Full Clean and formal protocol approval remain pending"]}
    pod = {"layer": "POD", "target": "recommendation Pod",
        "status": "ENGINEERING_READY" if two_pods_covered and cgroup and safe and smoke
                  else "CONDITIONAL",
        "evidence": {"target_and_sibling_30_server_spans_each_minute": two_pods_covered,
            "two_uid_container_cgroup_relations_verified": cgroup,
            "bounded_memory_headroom_each_pod": [row[
                "bounded_memory_headroom_bytes"] for row in data["cgroup"]["pods"]],
            "same_service_sibling_control": two_pods_covered,
            "memory_pressure_adapter_staged_read_only": True},
        "blockers": ["Engineering readiness is not fault qualification; two independent fault/recovery runs remain",
            "Full Clean and formal protocol approval remain pending"]}
    node = {"layer": "NODE", "target": "k8s-worker1",
        "status": "CONDITIONAL" if data["worker1"]["status"] ==
            "WORKER1_RESOURCE_BASELINE_OBSERVED" and safe else "BLOCKED",
        "evidence": {"ready_business_pods": len(data["worker1"]["ready_business_pods"]),
            "logical_cpus": data["worker1"]["logical_cpus"],
            "cpu_psi_some_avg60": data["worker1"]["cpu_psi"]["some"]["avg60"],
            "node_ready": data["topology"]["nodes"]["k8s-worker1"]["conditions"]["Ready"],
            "cpu_contention_adapter_staged_read_only": True},
        "mechanism_reason_code": "NODE_PENDING_SAFETY_CONTRACT",
        "blockers": ["Intentional Node CPU contention needs its own approved During safety/abort contract",
            "No real Node stress is authorized"]}
    network = {"layer": "NETWORK_PATH", "target":
        "NETWORK_PATH/k8s-worker2/k8s-worker3",
        "status": "BLOCKED", "evidence": {
            "current_uid_route_endpoint_packets_proven": data["network"][
                "current_target_path_proven"],
            "target_checkout_cart_edge_spans_by_minute": data["distribution"][
                "client_server_edges_by_minute"].get("checkout -> cart"),
            "config_driven_adapter_dry_run": data["network"]["status"] ==
                "W2_W3_ADAPTER_DRY_RUN_SCOPE_PASS",
            "image_control": "CONDITIONAL",
            "payment_worker1_control": data["control"]["status"],
            "two_independent_controls_qualified": False},
        "blockers": ["Image breached prior full-window p95 rule",
            "Payment Worker1 is a new exploratory candidate without prior registration or full-window stability proof",
            "Filter effect and shared-resource independence need a bounded future rehearsal; no netem injected"]}
    value = {"status": "FAST_READY_PARTIAL_ENGINEERING_ONLY",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "layers": {row["layer"]: row for row in (service, pod, node, network)},
        "GLOBAL_SAFETY_STATUS": data["safety"]["status"],
        "OBSERVABILITY_STATUS": data["smoke"]["status"],
        "COLLECTOR_LONG_TERM_RELIABILITY": "UNRESOLVED_ACTIVE_REFUSALS_OBSERVED",
        "CLEAN_PROTOCOL_STATUS": data["clean"]["status"],
        "FORMAL_QUALIFICATION_ALLOWED": False,
        "TARGET_ENVIRONMENT_ENGINEERING_READY": False,
        "source_sha256": {str(path): sha(path) for path in SOURCES.values()},
        "faults_injected": 0, "formal_cases_created": 0,
        "test_gt_read": False, "rca_predictions_run": 0,
        "stage5_v3_frozen": False}
    OUT.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
        "layers": {name: item["status"] for name, item in value["layers"].items()},
        "formal_allowed": False}))


if __name__ == "__main__":
    main()
