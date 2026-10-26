#!/usr/bin/env python3
"""Master-local immutable +5/+60/+120/+240 Trace acquisition service."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import yaml


sys.path.insert(0, "/root/stage5-v3-collector-reliability/scripts")
from capture_continuous_traces import capture  # noqa: E402


TARGETS = ("cart", "checkout", "shipping", "quote")
STAGES = (("first_plus5", 5, 10, 60), ("supplement_plus60", 60, 65, 120),
          ("scoring_plus120", 120, 125, 180), ("audit_plus240", 240, 245, 300))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    if path.exists():
        raise ValueError("Refusing overwrite: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def issued(run):
    rows = []
    with (run / "state-machine/sessions.jsonl").open() as stream:
        for line in stream:
            session = json.loads(line)
            rows.extend({"stream": "state-machine", **step} for step in session["steps"])
    for name in ("image_asset", "cart_view"):
        with (run / "controls" / (name + ".jsonl")).open() as stream:
            rows.extend({"stream": name, **json.loads(line)} for line in stream)
    ids = [row["trace_id"] for row in rows if row.get("trace_id")]
    if len(ids) != len(set(ids)) or not 1000 <= len(ids) <= 10000:
        raise ValueError("Issued Trace ID denominator absent, duplicated, or too large")
    return rows, ids


def summarize(rows, spans, pod_uids, start):
    issued_ids = {row["trace_id"] for row in rows if row.get("trace_id")}
    proxy, frontend, image, cart = set(), set(), set(), set()
    uid = {name: {"server_spans": 0, "direct_uid_verified": 0,
                  "missing_uid": 0, "mismatched_uid": 0} for name in TARGETS}
    per_minute = [{"minute": i, "issued": 0, "proxy_matched": 0} for i in range(3)]
    minute_of = {}
    for row in rows:
        if not row.get("trace_id"):
            continue
        completed = datetime.fromisoformat(row.get("time_utc") or row.get(
            "scheduled_utc")).timestamp()
        latency = row.get("latency_ms")
        begun = completed - latency / 1000 if isinstance(latency, (int, float)) else completed
        minute = int((begun - start) // 60)
        if not 0 <= minute < 3:
            raise ValueError("Issued request outside preregistered window")
        minute_of[row["trace_id"]] = minute
        per_minute[minute]["issued"] += 1
    for span in spans:
        if (span.get("tag") or {}).get("span@kind") != "server":
            continue
        trace, service = span["traceID"], span["process"]["serviceName"]
        if service == "frontend-proxy":
            proxy.add(trace)
        elif service == "frontend":
            frontend.add(trace)
        elif service == "image-provider":
            image.add(trace)
        elif service == "cart":
            cart.add(trace)
        if service in uid:
            uid[service]["server_spans"] += 1
            tags = {tag.get("key"): tag.get("value") for tag in
                    span["process"].get("tags") or []}
            actual = tags.get("k8s.pod.uid")
            if not actual:
                uid[service]["missing_uid"] += 1
            elif actual in pod_uids[service]:
                uid[service]["direct_uid_verified"] += 1
            else:
                uid[service]["mismatched_uid"] += 1
    for trace in proxy & issued_ids:
        per_minute[minute_of[trace]]["proxy_matched"] += 1
    controls = {name: {row["trace_id"] for row in rows if row["stream"] == name and
                        row.get("trace_id")}
                for name in ("image_asset", "cart_view")}
    return {"issued_request_rows": len(rows), "issued_trace_ids": len(issued_ids),
        "successful_request_rows": sum(bool(row.get("success")) for row in rows),
        "proxy_server_matched": len(proxy & issued_ids),
        "missing_proxy": sorted(issued_ids - proxy),
        "fixed_image_backend_missing": sorted(controls["image_asset"] - image),
        "fixed_cart_backend_missing": sorted(controls["cart_view"] - cart),
        "fixed_cart_frontend_missing": sorted(controls["cart_view"] - frontend),
        "uid_counts": uid, "per_minute": per_minute}


def check_stage_timing(offset, latest_start, latest_complete, actual_start, completed, end):
    start_lag, completion_lag = actual_start - end, completed - end
    if not offset <= start_lag <= latest_start or completion_lag > latest_complete:
        raise ValueError("Trace stage missed preregistered deadline: " +
                         str((start_lag, completion_lag)))
    return {"start_lag_seconds": start_lag, "completion_lag_seconds": completion_lag}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    from data_disk_guard import assert_ready
    assert_ready(args.run)
    assert_ready(args.output)
    if args.output.parent != args.run or args.output.name != "trace":
        raise ValueError("Trace output must be inside the registered mounted run")
    policy = yaml.safe_load(args.prereg.read_text())
    schedule = json.loads((args.run / "schedule.json").read_text())
    if (policy["id"] != schedule["id"] or policy["status"] !=
            "PREREGISTERED_NO_FAULT_CANARY" or schedule["fault_injected"] or
            policy["trace_completion"]["offsets_seconds"] != [5, 60, 120, 240]):
        raise ValueError("Trace schedule differs from registration")
    start = datetime.fromisoformat(schedule["scheduled_start_utc"]).timestamp()
    end = datetime.fromisoformat(schedule["scheduled_end_utc"]).timestamp()
    if not 170 <= end - start <= 190 or time.time() > end:
        raise ValueError("Trace service started after the registered window")
    args.output.mkdir(parents=True)
    result = {"run_id": schedule["id"], "status": "RUNNING",
        "trace_source": "JAEGER_OPENSEARCH_STORAGE",
        "scheduled_start_utc": schedule["scheduled_start_utc"],
        "scheduled_end_utc": schedule["scheduled_end_utc"],
        "source_sha256": {str(path): sha(path) for path in (
            args.prereg, args.run / "schedule.json", Path(__file__))},
        "stages": {}, "fault_injected": False}
    prior_ids = set()
    prior_source = None
    try:
        for name, offset, latest_start, latest_complete in STAGES:
            time.sleep(max(0, end + offset - time.time()))
            actual_start = time.time()
            if actual_start - end > latest_start:
                raise ValueError(name + " started after deadline")
            for expected in (args.run / "state-machine/result-manifest.json",
                             args.run / "controls/result-manifest.json"):
                if not expected.is_file():
                    raise ValueError("Issued workload manifest absent at " + name)
            assert_ready(args.output)
            rows, ids = issued(args.run)
            destination = args.output / name
            destination.mkdir()
            index = "jaeger-jaeger-span-" + datetime.fromtimestamp(end,
                timezone.utc).strftime("%Y-%m-%d")
            spans, identities, source = capture(index, ids,
                int(start * 1_000_000) - 1_000_000,
                int(end * 1_000_000) + 5_000_000, destination)
            completed = time.time()
            timing = check_stage_timing(offset, latest_start, latest_complete,
                                        actual_start, completed, end)
            keys = set(identities)
            if prior_ids - keys:
                raise ValueError("Later capture lost earlier TraceID/SpanID")
            source_id = (source["index"], source["index_uuid"],
                         source["query_start_us"], source["query_end_us"])
            if prior_source is not None and source_id != prior_source:
                raise ValueError("Trace source or query window changed")
            summary = summarize(rows, spans, schedule["pod_uids"], start)
            if source["expected_hits"] != len(keys) or source["actual_hits"] != len(keys):
                raise ValueError("Duplicate or incomplete storage span page")
            stage = {"status": "TRACE_STAGE_FROZEN", "name": name,
                "actual_start_utc": datetime.fromtimestamp(actual_start,
                    timezone.utc).isoformat(),
                "completed_utc": datetime.fromtimestamp(completed,
                    timezone.utc).isoformat(), **timing,
                "source": source, "summary": summary,
                "new_span_identities": len(keys - prior_ids),
                "lost_prior_span_identities": 0,
                "span_identity_sha256": hashlib.sha256(json.dumps(sorted(keys),
                    separators=(",", ":")).encode()).hexdigest(),
                "issued_sessions_sha256": sha(args.run / "state-machine/sessions.jsonl"),
                "issued_image_sha256": sha(args.run / "controls/image_asset.jsonl"),
                "issued_cart_sha256": sha(args.run / "controls/cart_view.jsonl"),
                "private_gt_read": False}
            save(destination / "stage-manifest.json", stage)
            result["stages"][name] = {"status": stage["status"],
                "manifest_sha256": sha(destination / "stage-manifest.json"),
                "unique_spans": len(keys), **timing}
            prior_ids, prior_source = keys, source_id
        result["status"] = "TIMED_TRACE_CAPTURE_READY"
        result["late_after_scoring_count"] = (result["stages"]["audit_plus240"][
            "unique_spans"] - result["stages"]["scoring_plus120"]["unique_spans"])
    except Exception as error:
        result["status"] = "TIMED_TRACE_CAPTURE_FAIL_CLOSED"
        result["error"] = str(error)
        raise
    finally:
        result["completed_utc"] = datetime.now(timezone.utc).isoformat()
        save(args.output / "trace-schedule.json", result)
        print(json.dumps({"status": result["status"],
            "stages": list(result["stages"]), "error": result.get("error")}))


if __name__ == "__main__":
    main()
