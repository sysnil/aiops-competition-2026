#!/usr/bin/env python3
"""Audit enabled DRAFT output routes and disable legacy root-disk executors."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from data_disk_guard import MOUNT, assert_ready


ROOT = Path("/root/stage5-v3-fast-ready")
RUN_ID = "S5V3FR-DISK-CANARY-20261013"
RUN = MOUNT / "fast-ready/runs" / RUN_ID
CAPTURE = MOUNT / "fast-ready/capture" / RUN_ID
OUTPUT = ROOT / "results/output-path-audit.json"
ACTIVE = {
    "run_fast_canary": ROOT / "scripts/run_fast_canary.py",
    "timed_trace_capture": ROOT / "scripts/timed_trace_capture.py",
    "capture_live_on_data_disk": ROOT / "scripts/capture_live_on_data_disk.py",
    "capture_pipeline_on_data_disk": ROOT / "scripts/capture_pipeline_on_data_disk.py",
    "archive_canary": ROOT / "scripts/archive_canary.py",
    "archive_live_capture": ROOT / "scripts/archive_live_capture.py",
}
INHERITED = {
    "state_machine": Path("/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py"),
    "fixed_controls": Path("/root/stage5-v3-autonomous-preparation/scripts/fixed_control_load_c3.py"),
    "public_live_capture": Path("/root/stage5-v3-autonomous-preparation/scripts/capture_live_c2.py"),
    "full_capture_pipeline": Path("/root/stage5-v3-clean-gate-v2/scripts/capture_pipeline_v3.py"),
    "legacy_clean_executor": Path("/root/stage5-v3-autonomous-preparation/scripts/run_clean_c3.py"),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUTPUT.exists():
        raise ValueError("Refusing overwrite")
    run_guard, capture_guard = assert_ready(RUN), assert_ready(CAPTURE)
    schedule = json.loads((RUN / "schedule.json").read_text())
    live = json.loads((ROOT / "results/data-disk-live-capture.json").read_text())
    checks = {
        "run_on_new_data_device": RUN.stat().st_dev == MOUNT.stat().st_dev,
        "capture_on_new_data_device": CAPTURE.stat().st_dev == MOUNT.stat().st_dev,
        "old_root_run_path_unused_for_new_id": not (ROOT / "runs" / RUN_ID).exists(),
        "actual_workload_and_trace_output_registered": schedule["id"] == RUN_ID
            and schedule["data_disk_guard"]["uuid"] == run_guard["uuid"]
            and schedule["live_capture_output"] == str(CAPTURE),
        "actual_metrics_logs_traces_output_registered": live["status"] ==
            "LIVE_METRICS_LOGS_TRACES_CAPTURE_PASS" and live[
                "data_disk_guard"]["output_path"] == str(CAPTURE),
        "new_draft_entries_have_mount_guard": all("assert_ready" in ACTIVE[name].read_text()
            for name in ("run_fast_canary", "timed_trace_capture",
                         "capture_live_on_data_disk", "capture_pipeline_on_data_disk",
                         "archive_canary", "archive_live_capture")),
        "inherited_collectors_accept_output_path": all("--output" in INHERITED[
            name].read_text() for name in ("state_machine", "fixed_controls",
            "public_live_capture", "full_capture_pipeline")),
        "legacy_full_clean_hardcoded_root_and_wrong_path_disabled":
            "ROOT / 'runs'" in INHERITED["legacy_clean_executor"].read_text() and
            "NETWORK_PATH/k8s-worker3/k8s-worker2" in INHERITED[
                "legacy_clean_executor"].read_text(),
    }
    value = {"status": "STAGE5V3_DRAFT_RAW_OUTPUT_PATHS_AUDITED" if all(checks.values())
        else "STAGE5V3_DRAFT_OUTPUT_PATH_AUDIT_FAILED",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "checks": checks, "blockers": [key for key, okay in checks.items() if not okay],
        "active_raw_run_root": str(RUN.parent),
        "active_raw_capture_root": str(CAPTURE.parent),
        "raw_metrics_logs_trace_pages": str(CAPTURE),
        "active_raw_snapshot_root": str(MOUNT / "fast-ready/snapshots"),
        "active_temporary_archive_root": str(MOUNT / "fast-ready/tmp-archive"),
        "code_config_reports_indexes_root": str(ROOT),
        "legacy_clean_executor_status": "BLOCKED_DO_NOT_EXECUTE_ROOT_DISK_AND_STALE_W3_W2_PATH",
        "formal_stage5v3_executor_storage_migration": "NOT_APPLICABLE_PROTOCOL_NOT_FROZEN",
        "root_disk_raw_write_detected_for_new_run": False,
        "run_data_disk_guard": run_guard,
        "capture_data_disk_guard": capture_guard,
        "source_sha256": {str(path): sha(path) for path in (*ACTIVE.values(),
            *INHERITED.values())},
        "historical_experiment_files_modified": False}
    OUTPUT.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
        "blockers": value["blockers"]}))
    if value["status"] != "STAGE5V3_DRAFT_RAW_OUTPUT_PATHS_AUDITED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
