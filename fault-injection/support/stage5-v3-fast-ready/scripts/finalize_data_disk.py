#!/usr/bin/env python3
"""Final Stage5-v3 disk deployment and no-fault readiness decision."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from data_disk_guard import MOUNT, UUID, assert_ready


ROOT = Path("/root/stage5-v3-fast-ready")
SOURCES = {
    "preflight": ROOT / "snapshots/master-new-disk-preflight.json",
    "mount": ROOT / "results/master-data-disk-mount.json",
    "validation": ROOT / "results/data-disk-validation.json",
    "paths": ROOT / "results/output-path-audit.json",
    "budget": ROOT / "results/clean-gate-data-disk-v2.json",
    "readiness": ROOT / "results/readiness-by-layer-data-disk.json",
    "collector": ROOT / "results/collector-metrics-after-data-disk-canary.json",
    "safety": ROOT / "results/final-data-disk-global-safety-v2.json",
    "residue": ROOT / "snapshots/final-data-disk-residue-v2.json",
    "tests": ROOT / "results/data-disk-offline-regression-v2.json",
    "summary": ROOT / "reports/fast-ready-after-data-disk.md",
    "deployment": ROOT / "reports/data-disk-deployment.md",
}
OUT = ROOT / "results/data-disk-final-decision.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUT.exists():
        raise ValueError("Refusing overwrite")
    data = {name: json.loads(path.read_text()) for name, path in SOURCES.items()
            if path.suffix == ".json"}
    guard = assert_ready(MOUNT / "fast-ready/runs/final-decision")
    now = datetime.now(timezone.utc)
    safety_age = (now - datetime.fromisoformat(data["safety"][
        "captured_at_utc"])).total_seconds()
    residue_age = (now - datetime.fromisoformat(data["residue"][
        "at_utc"])).total_seconds()
    checks = {
        "approved_disk_identity": data["preflight"]["status"] ==
            "APPROVED_BLANK_DISK_PREFLIGHT_PASS" and data["preflight"][
                "approved_disk"] == "/dev/sdb" and data["preflight"][
                "root_disk"] == "/dev/sda",
        "uuid_mount_fstab": data["mount"]["status"] ==
            "MASTER_STAGE5V3_DATA_MOUNT_VERIFIED" and data["mount"][
                "uuid"] == guard["uuid"] == UUID and data["mount"][
                "fstab_remount_test"] == "PASS",
        "actual_new_disk_capture_archive": data["validation"]["status"] ==
            "STAGE5V3_DATA_DISK_VALIDATED",
        "draft_output_paths": data["paths"]["status"] ==
            "STAGE5V3_DRAFT_RAW_OUTPUT_PATHS_AUDITED",
        "root_and_data_storage_budget": guard["root_free_bytes"] >= guard[
            "root_minimum_free_bytes"] and guard["data_free_bytes"] -
            5 * 1024 ** 3 >= guard["data_minimum_free_bytes"] and data[
            "budget"]["storage_preflight_pass"],
        "fresh_global_safety": data["safety"]["status"] ==
            "GLOBAL_SAFETY_PASS" and 0 <= safety_age <= 180,
        "fresh_five_host_residue": data["residue"]["status"] ==
            "HOST_RESIDUE_PASS" and 0 <= residue_age <= 180,
        "offline_tests": data["tests"]["status"] == "DATA_DISK_REGRESSION_PASS",
        "mechanism_decision_no_formal_fault": data["readiness"][
            "FORMAL_QUALIFICATION_ALLOWED"] is False,
    }
    root = shutil.disk_usage("/")
    disk = shutil.disk_usage(MOUNT)
    value = {"status": "STAGE5V3_DATA_DISK_VALIDATED_FAST_READY_PARTIAL"
        if all(checks.values()) else "STAGE5V3_DATA_DISK_OR_HEALTH_BLOCKED",
        "at_utc": now.isoformat(), "checks": checks,
        "blockers": [name for name, okay in checks.items() if not okay],
        "disk": {"device": "/dev/sdb1", "filesystem": "ext4", "uuid": UUID,
            "mountpoint": str(MOUNT), "total_bytes": disk.total,
            "available_bytes": disk.free,
            "run_root": str(MOUNT / "fast-ready/runs"),
            "capture_root": str(MOUNT / "fast-ready/capture"),
            "temporary_archive_root": str(MOUNT / "fast-ready/tmp-archive")},
        "root": {"device": "/dev/mapper/ubuntu--vg-ubuntu--lv",
            "physical_disk": "/dev/sda", "total_bytes": root.total,
            "available_bytes": root.free, "used_bytes": root.used,
            "existing_floor_bytes": guard["root_minimum_free_bytes"]},
        "GLOBAL_SAFETY_STATUS": data["safety"]["status"],
        "global_safety_captured_at_utc": data["safety"]["captured_at_utc"],
        "five_host_residue": data["residue"]["status"],
        "COLLECTOR_RELIABILITY_STATUS": "INTERMITTENT_REFUSAL_UNRESOLVED",
        "collector_limiter_observed_delta": data["collector"]["metrics"][
            "limiter_refused"]["observed_counter_delta"],
        "CLEAN_STORAGE_STATUS": data["budget"]["status"],
        "CLEAN_PROTOCOL_STATUS": "PENDING_HUMAN_APPROVAL_EXISTING_RULES_UNCHANGED",
        "readiness_by_layer": {name: item["status"] for name, item in
            data["readiness"]["layers"].items()},
        "FORMAL_QUALIFICATION_ALLOWED": False,
        "TARGET_ENVIRONMENT_ENGINEERING_READY": False,
        "source_sha256": {str(path): sha(path) for path in SOURCES.values()},
        "faults_injected": 0, "formal_cases_created": 0,
        "test_gt_read": False, "stage5_v3_frozen": False,
        "kubernetes_pv_containerd_observability_data_moved": False,
        "historical_raw_data_deleted": False}
    OUT.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
        "data_available_bytes": disk.free, "root_available_bytes": root.free,
        "blockers": value["blockers"]}))
    if value["status"] != "STAGE5V3_DATA_DISK_VALIDATED_FAST_READY_PARTIAL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
