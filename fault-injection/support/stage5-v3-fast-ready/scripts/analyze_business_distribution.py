#!/usr/bin/env python3
"""Pod-level ordinary-business coverage from immutable scoring Trace pages."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path


SERVICES = ("frontend", "recommendation", "checkout", "cart", "payment",
            "image-provider")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def issued_minutes(run, start):
    result = {}
    with (run / "state-machine/sessions.jsonl").open() as stream:
        for line in stream:
            for row in json.loads(line)["steps"]:
                if row.get("trace_id"):
                    completed = datetime.fromisoformat(row["time_utc"]).timestamp()
                    latency = row.get("latency_ms")
                    stamp = completed - latency / 1000 if isinstance(latency,
                        (int, float)) else completed
                    minute = int((stamp - start) // 60)
                    if not 0 <= minute < 3 or row["trace_id"] in result:
                        raise ValueError("Issued TraceID minute or identity invalid")
                    result[row["trace_id"]] = minute
    return result


def tags(span):
    return {tag.get("key"): tag.get("value") for tag in span["process"].get("tags") or []}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--topology", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    schedule = read(args.run / "schedule.json")
    topology = read(args.topology)
    source = args.run / "trace/scoring_plus120"
    manifest = read(source / "stage-manifest.json")
    if manifest["status"] != "TRACE_STAGE_FROZEN" or manifest[
            "start_lag_seconds"] > 125 or manifest["completion_lag_seconds"] > 180:
        raise ValueError("No timely scoring Trace snapshot")
    start = datetime.fromisoformat(schedule["scheduled_start_utc"]).timestamp()
    minutes = issued_minutes(args.run, start)
    spans = {}
    for path in sorted(source.glob("page-*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            page = json.load(stream)
        for hit in page["hits"]["hits"]:
            span = hit["_source"]
            key = (span["traceID"], span["spanID"])
            if key in spans:
                raise ValueError("Duplicate TraceID/SpanID")
            spans[key] = span
    expected_uids = {service: schedule["pod_uids"].get(service) or [item["uid"]
        for item in topology["service_pods"].get(service, []) if item["ready"]]
        for service in SERVICES}
    if any(not values for values in expected_uids.values()):
        raise ValueError("Public topology lacks a required business Pod")
    pod_counts = {service: {uid: [0, 0, 0] for uid in expected_uids[service]}
                  for service in SERVICES}
    unattributed = {service: [0, 0, 0] for service in SERVICES}
    edges = defaultdict(lambda: [0, 0, 0])
    for span in spans.values():
        minute = minutes.get(span["traceID"])
        if minute is None or (span.get("tag") or {}).get("span@kind") != "server":
            continue
        service = span["process"]["serviceName"]
        if service in SERVICES:
            uid = tags(span).get("k8s.pod.uid")
            if uid in pod_counts[service]:
                pod_counts[service][uid][minute] += 1
            else:
                unattributed[service][minute] += 1
        for ref in span.get("references") or []:
            parent = spans.get((ref.get("traceID"), ref.get("spanID")))
            if parent and (parent.get("tag") or {}).get("span@kind") == "client":
                parent_service = parent["process"]["serviceName"]
                edges[parent_service + " -> " + service][minute] += 1
                break
    value = {"status": "BUSINESS_DISTRIBUTION_AUDITED",
        "run_id": schedule["id"], "window": [schedule["scheduled_start_utc"],
            schedule["scheduled_end_utc"]],
        "scoring_capture_manifest_sha256": sha(source / "stage-manifest.json"),
        "state_machine_session_sha256": sha(args.run / "state-machine/sessions.jsonl"),
        "public_topology_sha256": sha(args.topology),
        "scoring_span_identities": len(spans),
        "per_pod_server_spans_by_minute": pod_counts,
        "unattributed_server_spans_by_minute": unattributed,
        "client_server_edges_by_minute": dict(edges),
        "recommendation_each_replica_30_per_minute": all(
            all(count >= 30 for count in counts)
            for counts in pod_counts["recommendation"].values()),
        "payment_each_replica_30_per_minute": all(
            all(count >= 30 for count in counts)
            for counts in pod_counts["payment"].values()),
        "fault_injected": False, "private_gt_read": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
        "recommendation_each_replica_30_per_minute": value[
            "recommendation_each_replica_30_per_minute"],
        "payment_each_replica_30_per_minute": value[
            "payment_each_replica_30_per_minute"]}))


if __name__ == "__main__":
    main()
