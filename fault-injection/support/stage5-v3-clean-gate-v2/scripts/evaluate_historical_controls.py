"""Compute exact request-level B/E candidates on verified historical control logs."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path("/data/rca-archives/stage5-v3-20261012")
MANIFEST = Path("/root/stage5-v3-archive-verify")
OUTPUT = Path("/root/stage5-v3-clean-gate-v2/results/historical-control-candidates.json")
SPECS = (
    ("S5V3AP-CLEAN-C2-S1", "autonomous-runs"),
    ("S5V3AP-CLEAN-C2-S2", "autonomous-runs"),
    ("S5V3AP-CLEAN-C3-S1", "autonomous-runs"),
    ("S5V3AP-CLEAN-C3-S2", "autonomous-runs"),
    ("S5V3AP-CAL-D3", "baseline-runs"),
    ("S5V3AP-VAL-1", "clean-val1"),
    ("S5V3AP-VAL-2", "clean-val2"),
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)] if ordered else None


def longest_run(values):
    best = current = 0
    for value in values:
        current = current + 1 if value else 0
        best = max(best, current)
    return best


def checked(path, root, manifest):
    relative = str(path.relative_to(root))
    if sha(path) != manifest["files"][relative]["sha256"]:
        raise RuntimeError("Control raw source hash mismatch: " + relative)
    return path


def assess_control(path, root, manifest, start):
    bins = [[] for _ in range(25)]
    for line in checked(path, root, manifest).read_text().splitlines():
        if not line:
            continue
        row = json.loads(line)
        time_text = row.get("time_utc") or row.get("scheduled_utc")
        minute = int((datetime.fromisoformat(time_text).timestamp() - start) // 60)
        if 0 <= minute < 25:
            bins[minute].append(row)
    successes = [[row["latency_ms"] for row in bucket if row.get("success") and
                  isinstance(row.get("latency_ms"), (int, float))]
                 for bucket in bins]
    if any(len(bucket) < 30 for bucket in successes):
        return {"status": "INSUFFICIENT_CONTROL_REQUESTS", "rules": {}}
    before = percentile([percentile(bucket, .95) for bucket in successes[:10]], .5)
    if before is None or before <= 0:
        raise ValueError("Invalid Before p95 reference")
    ratios = [percentile(bucket, .95) / before for bucket in successes[10:]]
    high = [ratio > 1.25 for ratio in ratios]
    error = any(not row.get("success") for bucket in bins for row in bucket)
    pooled = percentile([value for bucket in successes[10:] for value in bucket], .95)
    base = not error and all(len(bucket) >= 30 for bucket in successes)
    rules = {"A_old": base and all(value <= 1.25 for value in ratios),
        "B_persistence_peak": base and longest_run(high) <= 1 and
            max(ratios) <= 1.50,
        "E_B_and_window": base and longest_run(high) <= 1 and
            max(ratios) <= 1.50 and sum(high) / 15 <= .10 and
            pooled / before <= 1.25}
    return {"status": "CONTROL_REQUEST_SERIES_VALID", "rules": rules,
        "before_median_minute_p95_ms": before,
        "max_minute_p95_ratio": max(ratios),
        "high_minutes": sum(high), "longest_high_run": longest_run(high),
        "pooled_during_after_request_p95_ratio": pooled / before,
        "failed_requests": sum(not row.get("success") for bucket in bins for row in bucket),
        "minimum_successful_per_minute": min(len(bucket) for bucket in successes),
        "source_sha256": sha(path)}


def main():
    if OUTPUT.exists():
        raise ValueError("Refusing overwrite")
    rows = []
    manifests = {}
    for run_id, batch in SPECS:
        root = ROOT / batch
        run = root / run_id if batch in ("autonomous-runs", "baseline-runs") else root
        manifest_path = MANIFEST / (batch + "-source-manifest.json")
        manifest = manifests.setdefault(batch, json.loads(manifest_path.read_text()))
        schedule_path = checked(run / "load_pair/schedule.json", root, manifest)
        start = datetime.fromisoformat(json.loads(schedule_path.read_text())[
            "scheduled_start_utc"]).timestamp()
        controls = {name: assess_control(run / "load_pair/controls" /
            (name + ".jsonl"), root, manifest, start)
            for name in ("image_asset", "cart_view")}
        rules = {key: all(value["rules"].get(key, False) for value in
                controls.values()) for key in ("A_old", "B_persistence_peak",
                                          "E_B_and_window")}
        rows.append({"run_id": run_id, "batch": batch, "controls": controls,
                     "combined_rules_latency_and_client_only": rules,
                     "archive_source_manifest_sha256": sha(manifest_path)})
    value = {"status": "HISTORICAL_CANDIDATE_EXPLORATION", "at_utc":
        datetime.now(timezone.utc).isoformat(), "runs": rows,
        "client_latency_error_only": True,
        "trace_coverage_and_global_safety_still_separate": True,
        "history_reclassified": False}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": value["status"], "runs": [{
        "id": row["run_id"], "B": row[
            "combined_rules_latency_and_client_only"]["B_persistence_peak"],
        "E": row["combined_rules_latency_and_client_only"]["E_B_and_window"]}
        for row in rows]}))


if __name__ == "__main__":
    main()
