#!/usr/bin/env python3
"""Describe observed no-fault control jitter without changing its 1.25 gate."""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)] if ordered else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("No-fault noise summary must be new")
    grouped = defaultdict(list)
    per_run = []
    hashes = {}
    for path in args.analysis:
        value = json.loads(path.read_text())
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        row = {"run_id": value["run_id"], "controls": {}}
        for control, minutes in value["fixed_control_client"].items():
            ratios = [item["client_p95_to_clean_median"] for item in minutes[10:]
                      if item["client_p95_to_clean_median"] is not None]
            grouped[control].extend(ratios)
            row["controls"][control] = {
                "minutes_observed": len(ratios),
                "minutes_above_1_25": sum(x > 1.25 for x in ratios),
                "maximum_ratio": max(ratios) if ratios else None,
                "failed_requests": sum(item["attempted"] - item["successful"]
                                       for item in minutes),
                "minimum_successes_per_minute": min(item["successful"]
                                                    for item in minutes)}
        per_run.append(row)
    result = {"status": "DESCRIPTIVE_ONLY_NO_THRESHOLD_CHANGE",
        "current_p95_ratio_gate": 1.25, "runs": per_run,
        "pooled_controls": {name: {"minutes": len(values),
            "minutes_above_1_25": sum(value > 1.25 for value in values),
            "median_ratio": percentile(values, .5),
            "p95_ratio": percentile(values, .95),
            "p99_ratio": percentile(values, .99),
            "maximum_ratio": max(values) if values else None}
            for name, values in grouped.items()},
        "source_sha256": hashes,
        "not_a_new_admission_or_clean_gate": True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"],
                      "pooled_controls": result["pooled_controls"]}))


if __name__ == "__main__":
    main()
