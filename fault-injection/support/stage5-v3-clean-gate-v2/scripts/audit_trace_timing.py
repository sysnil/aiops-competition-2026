"""Audit immutable first reads, supplements, and delayed-index stability."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def seconds(later, earlier):
    return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds()


def analyze(run):
    phases = {}
    for phase in ("before", "during", "after"):
        root = run / "capture-pipeline"
        first_path = root / "captures" / phase / "capture-manifest.json"
        completion_path = root / "trace-completion" / phase / "completion-manifest.json"
        identity_path = root / "trace-completion" / phase / "identity-audit.json"
        lag_path = root / "ingest-lag" / (phase + ".json")
        first = read(first_path) if first_path.exists() else None
        completion = read(completion_path) if completion_path.exists() else None
        identity = read(identity_path) if identity_path.exists() else None
        lag = read(lag_path) if lag_path.exists() else None
        row = {"first_exists": bool(first), "supplement_exists": bool(completion),
               "lag_checks_exist": bool(lag)}
        if first:
            row.update({"first_start_lag_seconds": first["start_lag_seconds"],
                        "first_completion_lag_seconds": first["completion_lag_seconds"],
                        "first_span_count": first["span_records"],
                        "first_sha256": hashlib.sha256(first_path.read_bytes()).hexdigest(),
                        "first_crossed_plus_60": first["completion_lag_seconds"] > 60})
        if completion:
            row.update({"supplement_start_lag_seconds": completion[
                            "start_lag_seconds"],
                        "supplement_manifest_write_lag_seconds":
                            completion_path.stat().st_mtime - datetime.fromisoformat(
                                first["window"]["end_utc"]).timestamp() if first else None,
                        "late_span_count": completion["late_span_count"],
                        "first_hash_matches": bool(first) and completion[
                            "first_capture_sha256"] == row["first_sha256"],
                        "supplement_sha256": hashlib.sha256(
                            completion_path.read_bytes()).hexdigest()})
        if lag:
            samples = lag["samples"]
            counts = {str(x["planned_offset_seconds"]): x["trace_span_count"]
                      for x in samples}
            row.update({"trace_span_counts_by_offset": counts,
                        "index_count_changed_90_to_120": counts.get("90") !=
                             counts.get("120"),
                        "lag_sha256": hashlib.sha256(lag_path.read_bytes()).hexdigest()})
        if identity:
            row["identity_audit_status"] = identity["status"]
            row["identity_audit_sha256"] = hashlib.sha256(
                identity_path.read_bytes()).hexdigest()
        if first and completion and lag:
            count120 = row["trace_span_counts_by_offset"].get("120")
            row["unarchived_count_delta_at_plus_120"] = (
                count120 - completion["supplement_span_count"] if count120 is not None
                else None)
            row["candidate_timing_status"] = (
                "CAPTURE_INCOMPLETE_OR_LATE_ID_AUDIT_REQUIRED" if
                row["unarchived_count_delta_at_plus_120"] not in (None, 0) or
                identity and identity["status"] != "IDENTITY_PASS" else
                "BOUNDED_DELAY_COMPLETE" if completion["late_span_count"] > 0 else
                "LIVE_COMPLETE")
        row["status"] = ("FIRST_AND_SUPPLEMENT_PRESENT" if first and completion
                         else "CAPTURE_INCOMPLETE")
        phases[phase] = row
    return {"run_id": run.name, "phases": phases}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    value = {"status": "READ_ONLY_TIMING_AUDIT", "runs": [analyze(p) for p in
        args.run], "historic_result_reclassification": False,
        "plus_60_semantics": "supplement earliest start, not a completion deadline",
        "source": "capture and immutable completion manifests plus index lag checks"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "runs": len(value["runs"])}))


if __name__ == "__main__":
    main()
