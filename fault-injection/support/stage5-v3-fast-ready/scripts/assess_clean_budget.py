#!/usr/bin/env python3
"""Fail closed when a full immutable Clean window exceeds storage headroom."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil


GUARD_SOURCE = Path(__file__).with_name("data_disk_guard.py")


def assert_ready(path, planned_bytes):
    spec = importlib.util.spec_from_file_location("data_disk_guard", GUARD_SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.assert_ready(path, planned_bytes)


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decide(free, floor, examples, safety, smoke, data_free=None,
           data_floor=None, health_fresh=True):
    largest = max(examples)
    storage_safe = (free >= floor and
        (free - largest >= floor if data_free is None else
         data_free - largest >= data_floor))
    safety_ready = (safety == "GLOBAL_SAFETY_PASS" and
                    smoke == "OBSERVABILITY_SMOKE_PASS" and health_fresh)
    status = ("CLEAN_ENGINEERING_BLOCKED" if not storage_safe else
        "CLEAN_STORAGE_PASS_FRESH_HEALTH_REQUIRED" if not safety_ready else
        "CLEAN_STORAGE_PREFLIGHT_PASS")
    return {"status": status, "current_master_available_bytes": free,
        "existing_policy_minimum_free_bytes": floor,
        "prior_full_window_source_bytes": examples,
        "minimum_expected_temporary_bytes": largest,
        "projected_root_available_if_raw_written_on_root_bytes": free - largest,
        "data_disk_available_bytes": data_free,
        "data_disk_minimum_free_bytes": data_floor,
        "projected_data_available_after_prior_sized_window_bytes":
            data_free - largest if data_free is not None else None,
        "actual_raw_output_root": "/data/stage5-v3/fast-ready/runs" if
            data_free is not None else None,
        "storage_preflight_pass": storage_safe,
        "health_snapshot_fresh": health_fresh,
        "current_global_safety": safety,
        "short_observability_smoke": smoke,
        "full_clean_windows_started_this_round": 0,
        "full_clean_after_storage_relocation_permitted_by_this_check": False,
        "candidate_cpu_safety_v2_approved": False,
        "candidate_trace_completion_v2_approved": False,
        "old_minute_p95_rule_changed": False,
        "formal_qualification_allowed": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prior-manifest", type=Path, action="append", required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--global-safety", type=Path, required=True)
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or len(args.prior_manifest) != 2:
        raise ValueError("Two prior full windows and a new output required")
    policy = read(args.policy)
    examples = [read(path)["total_bytes"] for path in args.prior_manifest]
    safety, smoke = read(args.global_safety), read(args.smoke)
    health_age = (datetime.now(timezone.utc) - datetime.fromisoformat(
        safety["captured_at_utc"])).total_seconds()
    health_fresh = 0 <= health_age <= 180
    layout = read(Path("/root/stage5-v3-fast-ready/results/data-layout.json"))
    from_sources = (Path("/root/stage5-v3-fast-ready/scripts/run_fast_canary.py").read_text(),
                    Path("/root/stage5-v3-fast-ready/scripts/timed_trace_capture.py").read_text())
    if (layout["status"] != "STAGE5V3_DATA_DISK_GUARD_PASS" or
            not all("assert_ready" in source for source in from_sources)):
        raise ValueError("Data disk execution guard not installed")
    disk = assert_ready(Path("/data/stage5-v3/fast-ready/runs/clean-budget-precheck"),
                        max(examples))
    value = decide(shutil.disk_usage("/").free,
        policy["minimum_free_bytes_for_planned_runs"], examples,
        safety["status"], smoke["status"],
        disk["data_free_bytes"], disk["data_minimum_free_bytes"], health_fresh)
    value["data_disk_guard"] = disk
    value["data_disk_layout_sha256"] = sha(Path(
        "/root/stage5-v3-fast-ready/results/data-layout.json"))
    value.update({"assessed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": {str(path): sha(path) for path in (
            *args.prior_manifest, args.policy, args.global_safety, args.smoke)},
        "fault_injected": False})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
        "root_available_bytes": value["current_master_available_bytes"],
        "root_floor_bytes": value["existing_policy_minimum_free_bytes"],
        "data_available_bytes": value["data_disk_available_bytes"],
        "data_floor_plus_planned_bytes": value[
            "data_disk_minimum_free_bytes"] + value[
            "minimum_expected_temporary_bytes"]}))


if __name__ == "__main__":
    main()
