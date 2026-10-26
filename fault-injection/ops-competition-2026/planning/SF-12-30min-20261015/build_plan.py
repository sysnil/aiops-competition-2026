#!/usr/bin/env python3
"""Read-only capacity and mechanism audit for the proposed 12-case catalog."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
OPS = HERE.parents[1]
OLD = OPS / "batch-sf002-sf009-20261014"
FLOOR = 20_000_000_000
GIB = 1024**3
HOSTS = {"master": "192.168.53.52", "worker1": "192.168.53.53", "worker2": "192.168.53.54", "worker3": "192.168.53.55"}
SOCKETS = {"master": "/tmp/sfgen-master.sock", "worker1": "/tmp/sfgen-w1.sock", "worker2": "/tmp/sfgen-w2.sock", "worker3": "/tmp/sfgen-w3.sock"}
ADDITIONS = {
    "SF-010": ["ServicePause", "HTTP ReqDelay"],
    "SF-011": ["PodMemoryStress", "NetworkDelay"],
    "SF-012": ["NodeCPUStress", "HTTP RespReplaceCode"],
}
LAYERS = {
    "ServicePause": "SERVICE", "PodMemoryStress": "POD", "PodCPUStress": "POD",
    "PodKill": "POD", "ContainerKill": "POD", "NodeCPUStress": "NODE",
    "NetworkDelay": "NETWORK_PATH", "NetworkLoss": "NETWORK_PATH",
    "NetworkBandwidth": "NETWORK_PATH", "HTTP ReqDelay": "SERVICE",
    "HTTP RespDelay": "SERVICE", "HTTP ReqAbort": "SERVICE",
    "HTTP RespAbort": "SERVICE", "HTTP RespReplaceCode": "SERVICE",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def remote(host: str, code: str) -> object:
    result = subprocess.run(
        ["ssh", "-S", SOCKETS[host], "-o", "BatchMode=yes", f"root@{HOSTS[host]}",
         shlex.join(["python3", "-c", code])],
        capture_output=True, text=True, check=True, timeout=40,
    )
    return json.loads(result.stdout)


def snapshot_disks() -> dict:
    disks = {}
    for host in HOSTS:
        data = "/data/stage5-v3" if host == "master" else "/data"
        code = (
            "import json,shutil,os,time; "
            f"print(json.dumps({{'root_free_bytes':shutil.disk_usage('/').free,"
            f"'data_free_bytes':shutil.disk_usage('{data}').free,"
            f"'data_mount':'{data}','epoch':time.time()}}))"
        )
        disks[host] = remote(host, code)
        if min(disks[host]["root_free_bytes"], disks[host]["data_free_bytes"]) < FLOOR:
            raise RuntimeError(f"disk below authorized floor: {host}")
    return disks


def source_sizes() -> dict:
    code = r'''import json,pathlib
b=pathlib.Path('/data/stage5-v3/ops-competition-2026')
out={}
for case,run in [('SF-001','SF-PILOT-01-ATTEMPT-04'),('SF-002','SF-PILOT-02-ATTEMPT-01')]:
 p=b/('runs/'+run if case=='SF-001' else 'batch-sf002-sf009/SF-002')
 capture={'metrics':0,'logs':0,'traces_and_audits':0,'other':0}
 for f in (p/'capture').rglob('*'):
  if not f.is_file():continue
  rel=str(f.relative_to(p/'capture'))
  kind='metrics' if '/metrics/' in rel else 'logs' if '/logs/' in rel else 'traces_and_audits' if 'trace' in rel else 'other'
  capture[kind]+=f.stat().st_size
 runtime=b/('runtime/'+run if case=='SF-001' else 'batch-sf002-sf009/runtime/'+run)
 out[case]={'capture_bytes':capture,'raw_final_bytes':sum(f.stat().st_size for f in p.rglob('*') if f.is_file()),
 'runtime_bytes':sum(f.stat().st_size for f in runtime.rglob('*') if f.is_file()),
 'public_bytes':sum(f.stat().st_size for f in (p/'public').iterdir()),
 'private_bytes':sum(f.stat().st_size for f in (p/'private').rglob('*') if f.is_file())}
print(json.dumps(out))'''
    return remote("master", code)


def case_catalog(mechanisms: dict) -> list[dict]:
    cases = [
        {"case_id": "SF-001", "role": "PRACTICE", "duration_seconds": 3600,
         "collection_status": "COLLECTED", "fault_types": ["ServicePause", "PodMemoryStress"],
         "public_server_status": "PUBLISHED", "gt_status": "DRAFT_PENDING_HUMAN_REVIEW"},
        {"case_id": "SF-002", "role": "PRACTICE", "duration_seconds": 3600,
         "collection_status": "COLLECTED", "fault_types": ["ServicePause", "PodMemoryStress"],
         "public_server_status": "TRANSFER_AND_IMPORT_PENDING", "gt_status": "DRAFT_PENDING_HUMAN_REVIEW"},
    ]
    for number in range(3, 13):
        case_id = f"SF-{number:03d}"
        if case_id in ADDITIONS:
            types = ADDITIONS[case_id]
            original = False
        else:
            prior = json.loads((OLD / "plans" / f"{case_id}-candidate.json").read_text())
            types = [item["mechanism"] for item in prior["fault_events"]]
            original = True
        blockers = sorted({
            f"{kind}:{mechanisms[kind]['engineering_status']}"
            for kind in types if mechanisms[kind]["engineering_status"] != "QUALIFIED_EXISTING_ADAPTER_REUSED"
        })
        count = len(types)
        events = [
            {"fault_event_id": f"{case_id}-F{index:02d}", "fault_type": kind,
             "root_layer": LAYERS[kind],
             "duration_seconds": 300 if kind in {"ServicePause", "PodMemoryStress"} else None,
             "start_seconds": None, "target": None, "qualification": mechanisms[kind]["engineering_status"]}
            for index, kind in enumerate(types, 1)
        ]
        # The original first-chunk completion gate permits the first fault at
        # minute 12. Even two known 300-second events then need 32m15s.
        candidate = None
        conditional_candidate = [720, 1410] if case_id in {"SF-010", "SF-011"} else None
        time_status = (
            "BLOCKED_1935S_WITH_VALIDATED_300S_AND_FIRST_CHUNK_GATE"
            if count == 2 else "BLOCKED_OR_UNRESOLVED_PENDING_EACH_MECHANISM_TIME_CONTRACT"
        )
        cases.append({
            "case_id": case_id, "role": "OFFICIAL", "duration_seconds": 1800,
            "collection_status": "NOT_STARTED", "public_server_status": "NOT_CREATED",
            "gt_status": "NOT_CREATED", "fault_types": types, "fault_events": events,
            "original_combination_preserved": original if original else None,
            "production_ready": False, "mechanism_blockers": blockers,
            "time_plan_status": time_status, "candidate_starts_seconds": candidate,
            "candidate_is_authorized_schedule": False,
            "conditional_30min_starts_seconds": conditional_candidate,
            "conditional_second_fault_max_seconds_if_180s_recovery_and_45s_check": 165 if conditional_candidate else None,
            "new_case_design_status": "DRAFT_REQUIRES_HUMAN_REVIEW" if not original else None,
        })
    return cases


def budget(disks: dict, sizes: dict) -> dict:
    max_raw = max(item["raw_final_bytes"] for item in sizes.values())
    max_runtime = max(item["runtime_bytes"] for item in sizes.values())
    max_public = max(item["public_bytes"] for item in sizes.values())
    max_private = max(item["private_bytes"] for item in sizes.values())
    estimate = max_raw // 2 + max_runtime // 2
    stress = (estimate * 5 + 3) // 4  # +25%; sensitivity estimate, not a qualified peak envelope.
    demands = {"master": 34 * GIB, "worker2": 31 * GIB, "worker3": 29 * GIB}
    per_case_peak = {host: {
        "available_before_case_bytes": disks[host]["data_free_bytes"],
        "unchanged_60min_protection_bytes": demand,
        "remaining_after_protected_peak_bytes": disks[host]["data_free_bytes"] - demand,
        "passes_current_conservative_gate": disks[host]["data_free_bytes"] - demand >= FLOOR,
    } for host, demand in demands.items()}
    def admission_count(host: str) -> int:
        available = disks[host]["data_free_bytes"]
        count = 0
        for _ in range(10):
            if available - demands[host] < FLOOR:
                break
            count += 1
            available -= estimate
        return count
    def extra_needed(host: str, net: int) -> int:
        # Ten sequential cases: before case 10, nine final archives remain.
        return max(0, FLOOR + 9 * net + demands[host] - disks[host]["data_free_bytes"])
    return {
        "floor_bytes": FLOOR, "observed_hourly": sizes,
        "half_hour_linear_estimate_bytes_per_case_per_raw_archive_plus_runtime": estimate,
        "sensitivity_25pct_net_bytes_per_case": stress,
        "half_hour_linear_estimate_by_modality_bytes": {
            kind: max(item["capture_bytes"][kind] for item in sizes.values()) // 2
            for kind in ("metrics", "logs", "traces_and_audits", "other")
        },
        "half_hour_public_estimate_bytes": max_public // 2,
        "half_hour_private_estimate_bytes": max_private // 2,
        "ten_case_linear_cumulative_bytes_per_archive_host": 10 * estimate,
        "ten_case_25pct_cumulative_bytes_per_archive_host": 10 * stress,
        "current_validated_60min_peak_envelope_retained_until_30min_gate_is_tested": demands,
        "first_case_peak_checks": per_case_peak,
        "max_sequential_cases_at_linear_net_estimate_with_current_envelope": {
            host: admission_count(host) for host in demands
        },
        "additional_data_capacity_needed_for_tenth_case_with_current_envelope_linear": {
            host: extra_needed(host, estimate) for host in demands
        },
        "additional_data_capacity_needed_for_tenth_case_with_current_envelope_25pct": {
            host: extra_needed(host, stress) for host in demands
        },
        "half_hour_peak_not_validated": True,
        "model_limitations": [
            "Half-hour sizes are linear extrapolations from two one-hour cases with two faults each.",
            "More events, trace burst, normalization, and temporary files can increase size.",
            "A shorter collector/exporter envelope requires an independent live no-fault validation before use.",
            "Each case requires a fresh peak check on Master and both archive Workers.",
        ],
    }


def main() -> None:
    evidence = HERE / "evidence"
    evidence.mkdir(exist_ok=True)
    capabilities_path = evidence / "fault-capability-matrix.json"
    capabilities = {item["fault_type"]: item for item in json.loads(capabilities_path.read_text())}
    disks = snapshot_disks()
    sizes = source_sizes()
    cases = case_catalog(capabilities)
    resources = budget(disks, sizes)
    manifest = {
        "schema": "SF_12_CASES_30MIN_PRODUCTION_DRAFT_V1",
        "status": "DRAFT_PLANNING_AND_READ_ONLY_FEASIBILITY_AUDIT",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "absolute_disk_floor_bytes": FLOOR,
        "practice_cases": ["SF-001", "SF-002"], "official_cases": [f"SF-{i:03d}" for i in range(3, 13)],
        "official_execution_order_after_each_case_is_qualified": [f"SF-{i:03d}" for i in range(3, 13)],
        "official_count": 10, "practice_count": 2, "cases": cases,
        "storage": {"snapshot": disks, "budget": resources},
        "source_provenance": {
            "capability_matrix": {"path": str(capabilities_path), "sha256": sha(capabilities_path)},
            "old_candidate_plans": [
                {"path": str(path), "sha256": sha(path)} for path in sorted((OLD / "plans").glob("SF-00[3-9]-candidate.json"))
            ],
            "no_test_or_private_gt_access": True,
        },
        "time_contract": {
            "known_fault_duration_seconds": 300,
            "known_first_recovery_seconds": 240,
            "known_last_recovery_seconds": 180,
            "recovery_check_seconds": 45,
            "next_preflight_seconds": 105,
            "ten_minute_baseline_two_fault_minimum_seconds": 1815,
            "first_chunk_final_audit_and_quality_gate_earliest_first_fault_seconds": 720,
            "two_300_second_faults_with_validated_first_chunk_gate_minimum_seconds": 1935,
            "nine_minute_baseline_math_only_seconds": 1755,
            "nine_minute_baseline_candidate_blocked_by_first_chunk_gate": True,
            "three_300_second_faults_ten_minute_baseline_minimum_seconds": 2505,
            "no_overlap_or_duration_reduction_authorized": True,
        },
        "practice_publication": {
            "sf001_public_published": True,
            "sf002_public_on_master_only": True,
            "sf002_transfer_import_api_publish": "PLANNED_NOT_EXECUTED",
            "starter_agent_submit_py_end_to_end": "NOT_VERIFIED",
            "grader_practice_case_mapping": "CURRENTLY_DEMO_001_ONLY",
            "sf001_gt": "DRAFT_PENDING_HUMAN_REVIEW",
            "sf002_gt": "DRAFT_PENDING_HUMAN_REVIEW",
        },
        "no_formal_fault_started": True,
        "no_original_manifest_rewritten": True,
    }
    output = HERE / "SF-12-cases-30min-production-plan.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (evidence / "resource-snapshot.json").write_text(json.dumps({"disks": disks, "source_sizes": sizes}, indent=2) + "\n")
    print(json.dumps({"manifest": str(output), "status": manifest["status"],
                      "worker2_linear_max_cases": resources["max_sequential_cases_at_linear_net_estimate_with_current_envelope"]["worker2"]}))


if __name__ == "__main__":
    main()
