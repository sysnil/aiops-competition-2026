"""Independent, fail-closed Stage5-v3 rehearsal safety decision."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


def parse_time(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, TypeError, ValueError):
        return None


def assess(snapshot, policy, now=None):
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    expected = set(policy["expected_nodes"])
    checks = {}

    captured = parse_time(snapshot.get("captured_at_utc"))
    checks["snapshot_fresh"] = (captured is not None and
        0 <= now - captured <= policy["max_snapshot_age_seconds"])

    nodes = snapshot.get("nodes", {})
    checks["exact_four_nodes"] = set(nodes) == expected
    checks["nodes_ready_pressure_free"] = checks["exact_four_nodes"] and all(
        nodes[name].get("Ready") == "True" and all(
            nodes[name].get(kind) == "False" for kind in
            ("DiskPressure", "MemoryPressure", "PIDPressure")) and
        not nodes[name].get("disk_pressure_taint", True)
        for name in expected)
    checks["node_conditions_fresh"] = checks["exact_four_nodes"] and all(
        (lambda stamp: stamp is not None and
         0 <= now - stamp <= policy["max_node_condition_age_seconds"])(
            parse_time(nodes[name].get("condition_heartbeat_utc")))
        for name in expected)
    checks["node_leases_fresh"] = checks["exact_four_nodes"] and all(
        (lambda stamp: stamp is not None and
         0 <= now - stamp <= policy["max_node_lease_age_seconds"])(
            parse_time(nodes[name].get("node_lease_renew_utc")))
        for name in expected)

    infra = snapshot.get("infra_agent", {})
    checks["infra_agent_4_of_4"] = (infra.get("desired") == 4 and
        infra.get("ready") == 4 and infra.get("available") == 4)

    metrics = snapshot.get("metrics", {})
    for name in policy["required_metric_names"]:
        values = metrics.get(name, {})
        checks["metric_" + name] = (set(values) == expected and all(
            row.get("sample_count", 0) >= policy["minimum_metric_samples_per_node"] and
            row.get("window_seconds", 0) >= policy["metric_window_seconds"] -
                policy["metric_step_seconds"] and
            (lambda stamp: stamp is not None and 0 <= now - stamp <=
                policy["metric_step_seconds"] * 2)(
                parse_time(row.get("latest_sample_utc")))
            for row in values.values()))

    storage = snapshot.get("storage", {})
    free = storage.get("available_bytes")
    capacity = storage.get("capacity_bytes")
    fraction = (free / capacity if isinstance(free, (int, float)) and
                isinstance(capacity, (int, float)) and capacity > 0 else None)
    checks["storage_capacity_for_plan"] = (fraction is not None and
        free >= policy["minimum_free_bytes_for_planned_runs"] and
        fraction >= policy["minimum_imagefs_free_fraction"] and
        storage.get("imagefs_available_fraction") is not None and
        storage["imagefs_available_fraction"] >= policy["minimum_imagefs_free_fraction"] and
        storage.get("inodes_free_fraction", 0) > 0.05)

    components = snapshot.get("components", {})
    for name in policy["required_components"]:
        checks["component_" + name] = components.get(name) == "PASS"

    return {"status": "GLOBAL_SAFETY_PASS" if all(checks.values()) else
            "GLOBAL_SAFETY_FAIL", "captured_at_utc": snapshot.get("captured_at_utc"),
            "checks": checks, "blockers": [key for key, passed in checks.items()
                                          if not passed], "gate_order": [
            "GLOBAL_SAFETY", "WORKLOAD_QUALIFICATION", "MECHANISM_READINESS",
            "OBSERVABILITY_QUALITY"], "faults_allowed": all(checks.values())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = assess(json.loads(args.snapshot.read_text()),
                   json.loads(args.policy.read_text()))
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": value["status"], "blockers": value["blockers"]}))


if __name__ == "__main__":
    main()
