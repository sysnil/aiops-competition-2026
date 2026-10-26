#!/usr/bin/env python3
"""Compare immutable +120 and +240 issued-business Trace snapshots."""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def identities(root, manifest):
    result = {}
    for name, row in manifest["source"]["raw_pages"].items():
        page = root / name
        if not page.is_file() or sha(page) != row["sha256"]:
            raise ValueError("Frozen compressed Span page hash mismatch: " + name)
        with gzip.open(page, "rt", encoding="utf-8") as source:
            response = json.load(source)
        for hit in response["hits"]["hits"]:
            span = hit["_source"]
            key = (span["traceID"], span["spanID"])
            digest = hashlib.sha256(json.dumps(span, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()
            if key in result and result[key] != digest:
                raise ValueError("Conflicting duplicate Span identity")
            result[key] = digest
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--late", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    first_path = args.first / "capture-manifest.json"
    late_path = args.late / "capture-manifest.json"
    first, late = [json.loads(path.read_text()) for path in (first_path, late_path)]
    if (first["stage"] != "first" or late["stage"] != "late" or first["run_id"] != late[
            "run_id"] or first["source"]["index_uuid"] != late["source"]["index_uuid"] or
            first["source"]["query_sha256"] != late["source"]["query_sha256"]):
        raise ValueError("Different source, index or query between frozen captures")
    a, b = identities(args.first, first), identities(args.late, late)
    added = sorted(set(b) - set(a))
    lost = sorted(set(a) - set(b))
    changed = sorted(key for key in set(a) & set(b) if a[key] != b[key])
    scheduled_end = datetime.fromisoformat(first["scheduled_end_utc"]).timestamp()
    first_end = datetime.fromisoformat(first["source"]["read_completed_utc"]).timestamp()
    late_start = datetime.fromisoformat(late["source"]["read_started_utc"]).timestamp()
    draft_first_deadline_pass = first_end <= scheduled_end + 180
    late_start_after_240 = late_start >= scheduled_end + 240
    status = "CONTINUOUS_TRACE_COMPLETION_PASS" if (not added and not lost and not changed
        and draft_first_deadline_pass and late_start_after_240 and first["status"] ==
        late["status"] == "TRACE_CAPTURE_COMPLETE") else "CONTINUOUS_TRACE_COMPLETION_INCOMPLETE"
    result = {"status": status, "at_utc": datetime.now(timezone.utc).isoformat(),
        "first_manifest_sha256": sha(first_path), "late_manifest_sha256": sha(late_path),
        "first_identities": len(a), "late_identities": len(b),
        "critical_added_identity_count": len(added), "lost_identity_count": len(lost),
        "conflicting_identity_count": len(changed),
        "added_examples": added[:20], "lost_examples": lost[:20],
        "first_read_completed_after_end_seconds": first_end - scheduled_end,
        "first_scoring_snapshot_frozen_by_plus_180_draft": draft_first_deadline_pass,
        "late_read_started_after_end_seconds": late_start - scheduled_end,
        "late_read_started_after_plus_240": late_start_after_240,
        "late_read_used_to_replace_first": False,
        "private_gt_read": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("status", "first_identities",
        "late_identities", "critical_added_identity_count", "lost_identity_count",
        "first_read_completed_after_end_seconds")}))
    if status != "CONTINUOUS_TRACE_COMPLETION_PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
