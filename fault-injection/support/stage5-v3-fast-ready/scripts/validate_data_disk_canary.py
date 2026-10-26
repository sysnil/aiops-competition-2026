#!/usr/bin/env python3
"""Verify new-disk capture, SHA archives, and root/data usage separation."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil

from data_disk_guard import MOUNT, UUID, assert_ready


ROOT = Path("/root/stage5-v3-fast-ready")
RUN_ID = "S5V3FR-DISK-CANARY-20261013"
RUN = MOUNT / "fast-ready/runs" / RUN_ID
CAPTURE = MOUNT / "fast-ready/capture" / RUN_ID
FILES = {
    "mount": ROOT / "results/master-data-disk-mount.json",
    "layout": ROOT / "results/data-layout.json",
    "schedule": RUN / "schedule.json",
    "trace": RUN / "trace/trace-schedule.json",
    "capture": CAPTURE / "capture-manifest.json",
    "capture_result": ROOT / "results/data-disk-live-capture.json",
    "smoke": ROOT / "results/data-disk-observability-smoke.json",
    "post_health": ROOT / "results/post-disk-canary-global-safety.json",
    "run_source": ROOT / "archive/data-disk-canary-source-manifest.json",
    "capture_source": ROOT / "archive/data-disk-live-capture-source-manifest.json",
    "run_w2": ROOT / "archive/worker2-disk-canary-proof.json",
    "run_w3": ROOT / "archive/worker3-disk-canary-proof.json",
    "capture_w2": ROOT / "archive/worker2-live-capture-proof.json",
    "capture_w3": ROOT / "archive/worker3-live-capture-proof.json",
}
OUT = ROOT / "results/data-disk-validation.json"


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def proofs(rows, source, status):
    return ({row["status"] for row in rows} == {status} and
        {row["worker_ip"] for row in rows} == {"192.168.53.54", "192.168.53.55"}
        and len({row["source_manifest_sha256"] for row in rows}) == 1 and
        rows[0]["source_manifest_sha256"] == sha(source))


def main():
    if OUT.exists():
        raise ValueError("Refusing overwrite")
    data = {name: read(path) for name, path in FILES.items()}
    guard = assert_ready(RUN)
    capture_guard = assert_ready(CAPTURE)
    mounted = data["mount"]
    before_root = data["schedule"]["data_disk_guard"]["root_free_bytes"]
    before_data = data["schedule"]["data_disk_guard"]["data_free_bytes"]
    after_root = shutil.disk_usage("/").free
    after_data = shutil.disk_usage(MOUNT).free
    run_bytes = data["run_source"]["source_bytes"]
    capture_bytes = data["capture_source"]["source_bytes"]
    root_decline = before_root - after_root
    data_decline = before_data - after_data
    checks = {
        "approved_mount_uuid_ext4_fstab_remount": mounted["uuid"] == UUID and
            mounted["filesystem"] == "ext4" and mounted["fstab_remount_test"] ==
            "PASS" and guard["uuid"] == capture_guard["uuid"] == UUID,
        "distinct_root_data_filesystems": guard["root_device"] != guard[
            "data_device"] and os.stat(RUN).st_dev == os.stat(CAPTURE).st_dev ==
            os.stat(MOUNT).st_dev,
        "registered_raw_paths": data["schedule"]["id"] == RUN_ID and
            data["capture_result"]["data_disk_guard"]["output_path"] ==
            str(CAPTURE) and data["schedule"]["data_disk_guard"]["output_path"]
            == str(RUN),
        "live_metrics_logs_traces_kubernetes": data["capture_result"]["status"]
            == "LIVE_METRICS_LOGS_TRACES_CAPTURE_PASS" and all(
                data["capture_result"]["layout_counts"].get(name, 0) > 0
                for name in ("metrics", "logs", "trace-storage-pages", "kubernetes")),
        "immutable_timed_trace": data["trace"]["status"] ==
            "TIMED_TRACE_CAPTURE_READY" and set(data["trace"]["stages"]) ==
            {"first_plus5", "supplement_plus60", "scoring_plus120", "audit_plus240"},
        "smoke_quality_pass": data["smoke"]["status"] == "OBSERVABILITY_SMOKE_PASS",
        "run_dual_worker_archive": proofs((data["run_w2"], data["run_w3"]),
            FILES["run_source"], "CANARY_ARCHIVE_VERIFIED_AND_RESTORED"),
        "capture_dual_worker_archive": proofs((data["capture_w2"],
            data["capture_w3"]), FILES["capture_source"],
            "DATA_DISK_LIVE_CAPTURE_ARCHIVE_VERIFIED"),
        "root_remains_above_existing_floor": after_root >= guard[
            "root_minimum_free_bytes"],
        "data_remains_above_independent_floor": after_data >= guard[
            "data_minimum_free_bytes"],
        "raw_bytes_accounted_on_new_disk": data_decline >= run_bytes +
            capture_bytes and data_decline < run_bytes + capture_bytes +
            64 * 1024 ** 2,
        "root_did_not_absorb_raw_capture": root_decline < 64 * 1024 ** 2,
        "post_health": data["post_health"]["status"] == "GLOBAL_SAFETY_PASS",
    }
    value = {"status": "STAGE5V3_DATA_DISK_VALIDATED" if all(checks.values())
        else "STAGE5V3_DATA_DISK_VALIDATION_FAILED",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "checks": checks, "blockers": [name for name, okay in checks.items() if not okay],
        "mountpoint": str(MOUNT), "uuid": UUID, "filesystem": "ext4",
        "root_free_before_bytes": before_root, "root_free_after_bytes": after_root,
        "root_free_change_bytes": after_root - before_root,
        "data_free_before_bytes": before_data, "data_free_after_bytes": after_data,
        "data_free_change_bytes": after_data - before_data,
        "raw_run_bytes": run_bytes, "raw_capture_bytes": capture_bytes,
        "raw_total_bytes": run_bytes + capture_bytes,
        "data_disk_guard": guard,
        "live_capture": {"metrics_files": data["capture_result"]["layout_counts"]["metrics"],
            "logs": data["capture_result"]["log_records"],
            "spans": data["capture_result"]["span_records"],
            "traces": data["capture_result"]["trace_records"],
            "capture_start_lag_seconds": data["capture_result"][
                "capture_start_lag_seconds"]},
        "issued_and_proxy": [data["smoke"]["issued_requests"],
            data["smoke"]["proxy_server_matched"]],
        "source_sha256": {str(path): sha(path) for path in FILES.values()},
        "historical_data_deleted": False, "fault_injected": False}
    OUT.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
        "root_free_after_bytes": after_root,
        "data_free_after_bytes": after_data,
        "raw_total_bytes": value["raw_total_bytes"],
        "blockers": value["blockers"]}))
    if value["status"] != "STAGE5V3_DATA_DISK_VALIDATED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
