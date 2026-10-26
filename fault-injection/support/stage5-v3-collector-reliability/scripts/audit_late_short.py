#!/usr/bin/env python3
"""Read-only +240 audit; never changes the frozen +120 Canary result."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import yaml

from audit_short_canary import capture, issued, sha


def identities(directory, source):
    rows = set()
    for relative, expected in source["pages"].items():
        path = directory / relative
        if sha(path) != expected:
            raise ValueError("Frozen raw page hash mismatch")
        for hit in json.loads(path.read_text())["hits"]["hits"]:
            span = hit["_source"]
            key = (span["traceID"], span["spanID"])
            if key in rows:
                raise ValueError("Duplicate Trace/Span identity")
            rows.add(key)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    audit_path = args.run / "trace-audit/audit.json"
    first = json.loads(audit_path.read_text())
    schedule = json.loads((args.run / "schedule.json").read_text())
    prereg = yaml.safe_load(args.prereg.read_text())
    end = datetime.fromisoformat(schedule["scheduled_end_utc"]).timestamp()
    time.sleep(max(0, end + prereg["quality_gate"]["repeat_missing_query_at_end_plus_seconds"] - time.time()))
    if first["id"] != schedule["id"]:
        raise ValueError("First frozen audit not from same Canary")
    _, valid = issued(args.run)
    ids = [row["trace_id"] for row in valid]
    args.output.mkdir(parents=True)
    start_us = first["source"]["query_start_us"]
    end_us = first["source"]["query_end_us"]
    index = first["source"]["index"]
    late_spans, late_source = capture(index, ids, args.output, start_us, end_us)
    first_ids = identities(args.run / "trace-audit", first["source"])
    late_ids = set(late_spans)
    if late_source["index_uuid"] != first["source"]["index_uuid"]:
        raise ValueError("Jaeger index source changed between immutable reads")
    added = sorted(late_ids - first_ids)
    lost = sorted(first_ids - late_ids)
    status = "NO_CRITICAL_LATE_SPANS" if not added and not lost else (
        "CRITICAL_LATE_ISSUED_BUSINESS_SPANS" if added else "SOURCE_IDENTITY_LOSS")
    result = {"status": status, "at_utc": datetime.now(timezone.utc).isoformat(),
        "first_audit_status_unchanged": first["status"],
        "first_audit_sha256": sha(audit_path),
        "first_span_identities": len(first_ids), "plus240_span_identities": len(late_ids),
        "critical_added_identity_count": len(added), "lost_identity_count": len(lost),
        "critical_added_samples": added[:20], "lost_samples": lost[:20],
        "late_source": late_source, "scoring_snapshot_replaced": False,
        "private_gt_read": False}
    (args.output / "late-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("status", "first_span_identities",
        "plus240_span_identities", "critical_added_identity_count", "lost_identity_count")}))
    if status != "NO_CRITICAL_LATE_SPANS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
