#!/usr/bin/env python3
"""Independent four-layer readiness after mounted-disk validation."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path("/root/stage5-v3-fast-ready")
DATA = Path("/data/stage5-v3/fast-ready")
SOURCES = {
    "topology": DATA / "snapshots/post-disk-canary-topology-20261013/topology.json",
    "distribution": ROOT / "results/data-disk-business-distribution.json",
    "cgroup": ROOT / "results/recommendation-cgroup-disk.json",
    "worker1": ROOT / "results/worker1-resources-disk.json",
    "network": ROOT / "results/data-disk-network-path-dry-run.json",
    "control": ROOT / "results/data-disk-payment-control-exploratory.json",
    "smoke": ROOT / "results/data-disk-observability-smoke.json",
    "safety": ROOT / "results/final-data-disk-global-safety.json",
    "storage": ROOT / "results/data-disk-validation.json",
    "clean_budget": ROOT / "results/clean-gate-data-disk-v2.json",
    "collector": ROOT / "results/collector-metrics-after-data-disk-canary.json",
}
OUT = ROOT / "results/readiness-by-layer-data-disk.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUT.exists():
        raise ValueError("Refusing overwrite")
    data = {name: json.loads(path.read_text()) for name, path in SOURCES.items()}
    topo = data["topology"]["service_pods"]
    rec_uids = {row["uid"] for row in topo["recommendation"] if row["ready"]}
    rec_counts = data["distribution"]["per_pod_server_spans_by_minute"][
        "recommendation"]
    two_pod_traffic = (len(rec_uids) == 2 and rec_uids == set(rec_counts) and
        all(all(count >= 30 for count in values) for values in rec_counts.values()))
    direct_cgroup = data["cgroup"]["status"] == "RECOMMENDATION_CGROUP_PREFLIGHT_PASS"
    safety = data["safety"]["status"] == "GLOBAL_SAFETY_PASS"
    smoke = data["smoke"]["status"] == "OBSERVABILITY_SMOKE_PASS"
    storage = data["storage"]["status"] == "STAGE5V3_DATA_DISK_VALIDATED"
    layers = {
        "SERVICE": {"target": "recommendation", "status": "CONDITIONAL" if
            two_pod_traffic and safety and smoke and storage else "BLOCKED",
            "ordinary_business_server_spans_per_replica_by_minute": rec_counts,
            "blockers": ["Non-target service control not qualified under full-window stability rule",
                         "Service-wide direct fault evidence/recovery need two bounded rehearsals"]},
        "POD": {"target": "recommendation Pod", "status": "ENGINEERING_READY" if
            two_pod_traffic and direct_cgroup and safety and smoke and storage else
            "CONDITIONAL", "ordinary_business_server_spans_per_replica_by_minute":
            rec_counts, "bounded_memory_headroom_bytes": [row[
                "bounded_memory_headroom_bytes"] for row in data["cgroup"]["pods"]],
            "blockers": ["Two fault/recovery qualification seeds still required",
                         "Formal Clean/protocol gate not approved"]},
        "NODE": {"target": "k8s-worker1", "status": "CONDITIONAL" if
            data["worker1"]["status"] == "WORKER1_RESOURCE_BASELINE_OBSERVED"
            and safety and storage else "BLOCKED",
            "ready_business_pod_count": len(data["worker1"]["ready_business_pods"]),
            "cpu_psi_some_avg60": data["worker1"]["cpu_psi"]["some"]["avg60"],
            "mechanism_reason_code": "NODE_PENDING_SAFETY_CONTRACT",
            "blockers": ["Node during-fault CPU safety/abort contract not approved"]},
        "NETWORK_PATH": {"target": "NETWORK_PATH/k8s-worker2/k8s-worker3",
            "status": "BLOCKED", "checkout_to_cart_edge_spans_by_minute":
            data["distribution"]["client_server_edges_by_minute"].get(
                "checkout -> cart"),
            "current_route_packet_and_dry_run": data["network"]["status"] ==
                "W2_W3_ADAPTER_DRY_RUN_SCOPE_PASS",
            "candidate_payment_client_spans_by_minute": [item["n"] for item in
                data["control"]["checkout_to_payment_client_span_ms"]],
            "blockers": ["Two independent normal controls not qualified",
                "Payment Worker1 candidate needs preregistered full-window control stability",
                "New filter/shared-resource scope not tested under a bounded fault"]},
    }
    value = {"status": "FAST_READY_STORAGE_READY_MECHANISMS_PARTIAL",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "layers": layers,
        "GLOBAL_SAFETY_STATUS": data["safety"]["status"],
        "OBSERVABILITY_STATUS": data["smoke"]["status"],
        "STORAGE_STATUS": data["storage"]["status"],
        "CLEAN_STORAGE_STATUS": data["clean_budget"]["status"],
        "CLEAN_PROTOCOL_STATUS": "PENDING_HUMAN_APPROVAL_EXISTING_RULES_UNCHANGED",
        "COLLECTOR_RELIABILITY_STATUS": "INTERMITTENT_REFUSAL_UNRESOLVED",
        "FORMAL_QUALIFICATION_ALLOWED": False,
        "TARGET_ENVIRONMENT_ENGINEERING_READY": False,
        "source_sha256": {str(path): sha(path) for path in SOURCES.values()},
        "faults_injected": 0, "formal_cases_created": 0,
        "test_gt_read": False, "stage5_v3_frozen": False}
    OUT.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
        "layers": {name: row["status"] for name, row in layers.items()},
        "formal_allowed": False}))


if __name__ == "__main__":
    main()
