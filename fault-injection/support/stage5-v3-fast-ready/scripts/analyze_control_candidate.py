#!/usr/bin/env python3
"""Descriptive three-minute Image and Payment control latency, no qualification."""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(rows, fraction):
    values = sorted(rows)
    return values[int((len(values) - 1) * fraction)] if values else None


def rows_by_minute(run, start):
    values = [[], [], []]
    with (run / "controls/image_asset.jsonl").open() as stream:
        for line in stream:
            item = json.loads(line)
            if not item.get("success") or item.get("latency_ms") is None:
                continue
            stamp = (datetime.fromisoformat(item["time_utc"]).timestamp() -
                     item["latency_ms"] / 1000)
            minute = int((stamp - start) // 60)
            if not 0 <= minute < 3:
                raise ValueError("Image control request outside window")
            values[minute].append(item["latency_ms"])
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--distribution", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    schedule = json.loads((args.run / "schedule.json").read_text())
    distribution = json.loads(args.distribution.read_text())
    start = datetime.fromisoformat(schedule["scheduled_start_utc"]).timestamp()
    image = rows_by_minute(args.run, start)
    spans = {}
    source = args.run / "trace/scoring_plus120"
    for path in sorted(source.glob("page-*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            page = json.load(stream)
        for hit in page["hits"]["hits"]:
            span = hit["_source"]
            key = (span["traceID"], span["spanID"])
            if key in spans:
                raise ValueError("Duplicate TraceID/SpanID")
            spans[key] = span
    minute_of = {}
    with (args.run / "state-machine/sessions.jsonl").open() as stream:
        for line in stream:
            for item in json.loads(line)["steps"]:
                if not item.get("trace_id"):
                    continue
                completed = datetime.fromisoformat(item["time_utc"]).timestamp()
                latency = item.get("latency_ms")
                stamp = completed - latency / 1000 if isinstance(latency,
                    (int, float)) else completed
                minute_of[item["trace_id"]] = int((stamp - start) // 60)
    payment = [[], [], []]
    payment_target_uids = distribution["per_pod_server_spans_by_minute"]["payment"]
    for span in spans.values():
        if span["process"]["serviceName"] != "payment" or (
                span.get("tag") or {}).get("span@kind") != "server":
            continue
        minute = minute_of.get(span["traceID"])
        if minute is None or not 0 <= minute < 3:
            continue
        for ref in span.get("references") or []:
            parent = spans.get((ref.get("traceID"), ref.get("spanID")))
            if parent and parent["process"]["serviceName"] == "checkout" and (
                    parent.get("tag") or {}).get("span@kind") == "client":
                payment[minute].append(parent["duration"] / 1000)
                break
    value = {"status": "PAYMENT_CONTROL_EXPLORATORY_NOT_PREREGISTERED",
        "window": [schedule["scheduled_start_utc"], schedule["scheduled_end_utc"]],
        "image_request_client_ms": [{"minute": i, "n": len(values),
            "p50": percentile(values, .5), "p95": percentile(values, .95)}
            for i, values in enumerate(image)],
        "checkout_to_payment_client_span_ms": [{"minute": i, "n": len(values),
            "p50": percentile(values, .5), "p95": percentile(values, .95)}
            for i, values in enumerate(payment)],
        "payment_pod_server_spans_by_minute": payment_target_uids,
        "cannot_qualify_with_three_minutes_or_post_registered_candidate": True,
        "source_sha256": {str(path): sha(path) for path in (
            args.run / "schedule.json", args.run / "controls/image_asset.jsonl",
            source / "stage-manifest.json", args.distribution)},
        "fault_injected": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
        "image_counts": [len(item) for item in image],
        "payment_counts": [len(item) for item in payment]}))


if __name__ == "__main__":
    main()
