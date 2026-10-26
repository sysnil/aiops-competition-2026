"""Proposed immutable +120 scoring snapshot and fixed +240 late audit."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

from trace_identity import span_map, subset

OLD = Path("/root/ops-competition-2026/sf002-production-20261014/capture")
sys.path.insert(0, str(OLD))
from capture_live import OPENSEARCH, request_json, write_json  # noqa: E402
from trace_storage_reader import read_window  # noqa: E402


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def wait_for(offset, end):
    time.sleep(max(0, end + offset - time.time()))
    return time.time() - end


def same_source(first, middle, last, start, end):
    expected = (start.isoformat(), end.isoformat())
    for row in (first, middle, last):
        if ((row["query_start_utc"], row["query_end_utc"]) != expected or
                row["trace_source"] != "JAEGER_OPENSEARCH_STORAGE"):
            raise ValueError("UTC window or trace source drift")
    if first["indices"] != middle["indices"] or \
            middle["indices"] != last["indices"]:
        raise ValueError("Trace source index changed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("before", "during", "after"),
                        required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--plus60", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.audit_output.exists():
        raise ValueError("Immutable trace version already exists")
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    end = datetime.fromisoformat(args.end.replace("Z", "+00:00"))
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("Invalid UTC trace window")
    end_ts = end.timestamp()
    try:
        offset = wait_for(120, end_ts)
        if not 120 <= offset <= 125:
            raise RuntimeError("+120 task did not start within five seconds")
        first_manifest = args.first / "capture-manifest.json"
        middle_manifest = args.plus60 / "completion-manifest.json"
        middle_audit = args.plus60 / "identity-audit.json"
        if not all(path.is_file() for path in (first_manifest, middle_manifest,
                                               middle_audit)):
            raise RuntimeError("First or +60 immutable version incomplete")
        if json.loads(middle_audit.read_text())["status"] != "IDENTITY_PASS":
            raise RuntimeError("+60 identity audit failed")
        first_hash, middle_hash = sha(first_manifest), sha(middle_manifest)
        args.output.mkdir(parents=True)
        started = time.time()
        final_source = read_window(start, end, args.output, OPENSEARCH,
                                   request_json, write_json)
        finished = time.time()
        if finished - end_ts > 180:
            raise RuntimeError("+120 final scoring capture exceeded +180 deadline")
        first_source = json.loads((args.first /
            "trace-storage-source.json").read_text())
        middle_source = json.loads((args.plus60 /
            "trace-storage-source.json").read_text())
        same_source(first_source, middle_source, final_source, start, end)
        first_ids, middle_ids, final_ids = (span_map(path) for path in
                                           (args.first, args.plus60, args.output))
        subset(first_ids, middle_ids)
        subset(middle_ids, final_ids)
        if sha(first_manifest) != first_hash or sha(middle_manifest) != middle_hash:
            raise RuntimeError("Earlier immutable manifest changed")
        final = {"status": "FINAL_SNAPSHOT_FROZEN", "phase": args.phase,
            "planned_start_utc": utc(end_ts + 120),
            "actual_start_utc": utc(started), "completed_utc": utc(finished),
            "query_duration_seconds": finished - started,
            "first_manifest_sha256": first_hash,
            "plus60_manifest_sha256": middle_hash,
            "first_ids": len(first_ids), "plus60_ids": len(middle_ids),
            "final_ids": len(final_ids), "final_source": final_source,
            "original_versions_overwritten": False,
            "scoring_archive": str(args.output)}
        write_json(args.output / "final-manifest.json", final)

        audit_offset = wait_for(240, end_ts)
        if not 240 <= audit_offset <= 245:
            raise RuntimeError("+240 late audit did not start within five seconds")
        args.audit_output.mkdir(parents=True)
        audit_started = time.time()
        audit_source = read_window(start, end, args.audit_output, OPENSEARCH,
                                   request_json, write_json)
        audit_finished = time.time()
        if audit_finished - end_ts > 300:
            raise RuntimeError("+240 audit exceeded +300 deadline")
        same_source(first_source, final_source, audit_source, start, end)
        later_ids = span_map(args.audit_output)
        subset(final_ids, later_ids)
        late = []
        for key in sorted(later_ids.keys() - final_ids.keys()):
            row = later_ids[key][1]
            tags = {tag.get("key"): tag.get("value") for tag in
                    (row.get("process", {}).get("tags") or [])}
            late.append({"trace_id": key[0], "span_id": key[1],
                "service": row["process"]["serviceName"],
                "pod_uid": tags.get("k8s.pod.uid"),
                "operation": row.get("operationName"),
                "start_us": row["startTime"]})
        audit = {"status": "LATE_AUDIT_FROZEN_UNCLASSIFIED", "phase": args.phase,
            "planned_start_utc": utc(end_ts + 240),
            "actual_start_utc": utc(audit_started),
            "completed_utc": utc(audit_finished),
            "query_duration_seconds": audit_finished - audit_started,
            "final_manifest_sha256": sha(args.output / "final-manifest.json"),
            "final_ids": len(final_ids), "audit_ids": len(later_ids),
            "late_count": len(late), "late_ids": late,
            "audit_source": audit_source,
            "scoring_archive_changed_by_audit": False}
        write_json(args.audit_output / "late-audit-manifest.json", audit)
        print(json.dumps({"phase": args.phase, "final_ids": len(final_ids),
                          "late_count": len(late), "status": audit["status"]}))
    except Exception as exc:
        target = args.output if args.output.exists() else args.audit_output
        target.mkdir(parents=True, exist_ok=True)
        write_json(target / "terminal.json", {"status": "CAPTURE_INCOMPLETE",
            "phase": args.phase, "reason": type(exc).__name__ + ": " + str(exc),
            "at_utc": utc(time.time()), "original_versions_overwritten": False})
        raise


if __name__ == "__main__":
    main()
