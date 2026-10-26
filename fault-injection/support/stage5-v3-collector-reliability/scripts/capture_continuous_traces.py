#!/usr/bin/env python3
"""Capture 45-minute issued-business traces on Worker data disk at +120/+240."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import socket
import statistics
import time
import urllib.request


OPENSEARCH = "http://10.111.114.255:9200"
ROOT = Path("/data/rca-archives/stage5-v3-collector-reliability-20261012")
RUN_ID = "CR-CONTINUOUS-45M-20261012"
TARGETS = ("cart", "checkout", "shipping", "quote")


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(path.read_text())


def request_json(path, body=None):
    request = urllib.request.Request(OPENSEARCH + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def issued(run):
    requests = []
    with (run / "state-machine/sessions.jsonl").open() as source:
        for line in source:
            session = json.loads(line)
            requests.extend({"stream": "state-machine", "session_index": session["index"],
                **step} for step in session["steps"])
    for stream in ("image_asset", "cart_view"):
        with (run / "controls" / (stream + ".jsonl")).open() as source:
            requests.extend({"stream": stream, **json.loads(line)} for line in source)
    ids = [row["trace_id"] for row in requests if row.get("trace_id")]
    if len(ids) != len(set(ids)) or not 15000 <= len(ids) <= 60000:
        raise ValueError("Issued Trace ID denominator unexpected or duplicated")
    return requests, ids


def capture(index, ids, start_us, end_us, destination):
    settings = request_json("/" + index + "/_settings")
    index_uuid = settings[index]["settings"]["index"]["uuid"]
    query = {"size": 3000, "sort": ["_doc"], "query": {"bool": {"filter": [
        {"terms": {"traceID": ids}},
        {"range": {"startTime": {"gte": start_us, "lt": end_us}}}]}}}
    started = datetime.now(timezone.utc)
    response = request_json("/" + index + "/_search?scroll=3m", query)
    total = response["hits"]["total"]
    if total.get("relation") != "eq" or not 1 <= total["value"] <= 1500000:
        raise RuntimeError("Bounded exact Jaeger total unavailable")
    hashes = {}
    identity_hashes = {}
    span_rows = []
    scroll_id = response.get("_scroll_id")
    try:
        for page_number in range(500):
            if response["_shards"].get("failed"):
                raise RuntimeError("OpenSearch shard failure")
            path = destination / ("page-%05d.json.gz" % page_number)
            with gzip.open(path, "xt", encoding="utf-8", compresslevel=5) as output:
                json.dump(response, output, separators=(",", ":"))
                output.write("\n")
            hashes[path.name] = {"sha256": sha(path), "bytes": path.stat().st_size,
                                 "hit_count": len(response["hits"]["hits"])}
            for hit in response["hits"]["hits"]:
                span = hit["_source"]
                key = (span["traceID"], span["spanID"])
                raw_hash = hashlib.sha256(json.dumps(span, sort_keys=True,
                    separators=(",", ":")).encode()).hexdigest()
                if key in identity_hashes:
                    if identity_hashes[key] != raw_hash:
                        raise RuntimeError("Conflicting duplicate span identity")
                    continue
                identity_hashes[key] = raw_hash
                span_rows.append(span)
            if sum(item["hit_count"] for item in hashes.values()) == total["value"]:
                break
            if sum(item["hit_count"] for item in hashes.values()) > total["value"] or not scroll_id:
                raise RuntimeError("OpenSearch scroll count mismatch")
            response = request_json("/_search/scroll", {"scroll": "3m", "scroll_id": scroll_id})
            scroll_id = response.get("_scroll_id", scroll_id)
        else:
            raise RuntimeError("Trace page budget exhausted")
    finally:
        if scroll_id:
            try:
                request_json("/_search/scroll", {"scroll_id": [scroll_id]})
            except Exception:
                pass
    return span_rows, identity_hashes, {"index": index, "index_uuid": index_uuid,
        "query_sha256": hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest(),
        "query_start_us": start_us, "query_end_us": end_us,
        "expected_hits": total["value"], "actual_hits": sum(item["hit_count"] for item in hashes.values()),
        "unique_span_identities": len(identity_hashes), "raw_pages": hashes,
        "read_started_utc": started.isoformat(),
        "read_completed_utc": datetime.now(timezone.utc).isoformat()}


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)] if ordered else None


def summarize(requests, spans, schedule):
    issued_ids = {row["trace_id"] for row in requests if row.get("trace_id")}
    start = datetime.fromisoformat(schedule["scheduled_start_utc"]).timestamp()
    trace_minutes = {}
    per_minute = [{"minute": i, "issued": 0, "success": 0, "failed": 0,
                   "proxy_matched": 0, "image_client_ms": [], "cart_client_ms": [],
                   "image_issued": 0, "image_success": 0,
                   "cart_issued": 0, "cart_success": 0,
                   "image_edge_spans": 0, "cart_edge_spans": 0,
                   "w1_recommendation_client_spans": 0,
                   "w1_recommendation_client_ms": []} for i in range(45)]
    errors = []
    completion_cross_window = 0
    for row in requests:
        completion = datetime.fromisoformat(row.get("time_utc") or row.get(
            "scheduled_utc")).timestamp()
        stamp = completion - row["latency_ms"] / 1000 if isinstance(row.get(
            "latency_ms"), (int, float)) else completion
        completion_cross_window += completion >= start + 2700 and stamp < start + 2700
        minute = int((stamp - start) // 60)
        if not 0 <= minute < 45:
            errors.append({"trace_id": row.get("trace_id"), "reason": "REQUEST_OUTSIDE_SCHEDULE"})
            continue
        bucket = per_minute[minute]
        bucket["issued"] += bool(row.get("trace_id"))
        bucket["success"] += bool(row.get("success"))
        bucket["failed"] += not bool(row.get("success"))
        if row["stream"] in ("image_asset", "cart_view"):
            prefix = "image" if row["stream"] == "image_asset" else "cart"
            bucket[prefix + "_issued"] += bool(row.get("trace_id"))
            bucket[prefix + "_success"] += bool(row.get("success"))
        if row.get("trace_id"):
            trace_minutes[row["trace_id"]] = minute
        if row["stream"] in ("image_asset", "cart_view") and row.get("latency_ms") is not None:
            bucket[("image" if row["stream"] == "image_asset" else "cart") + "_client_ms"].append(
                row["latency_ms"])
    by_trace = defaultdict(list)
    by_identity = {}
    uid_counts = {service: Counter() for service in TARGETS}
    for span in spans:
        by_trace[span["traceID"]].append(span)
        by_identity[(span["traceID"], span["spanID"])] = span
        service = span["process"]["serviceName"]
        if service in uid_counts and (span.get("tag") or {}).get("span@kind") == "server":
            uid_counts[service]["server_spans"] += 1
            tags = {item.get("key"): item.get("value") for item in span["process"].get("tags") or []}
            uid = tags.get("k8s.pod.uid")
            uid_counts[service]["direct_uid_verified"] += uid in schedule[
                "initial_infrastructure"]["business_pod_uids"][service] if uid else False
            uid_counts[service]["missing_uid"] += not bool(uid)
            uid_counts[service]["mismatched_uid"] += bool(uid) and uid not in schedule[
                "initial_infrastructure"]["business_pod_uids"][service]
    proxy_ids = set()
    frontend_ids = set()
    backend_ids = {"image_asset": set(), "cart_view": set()}
    for trace_id, group in by_trace.items():
        for span in group:
            kind = (span.get("tag") or {}).get("span@kind")
            service = span["process"]["serviceName"]
            if service == "frontend-proxy" and kind == "server":
                proxy_ids.add(trace_id)
            if service == "frontend" and kind == "server":
                frontend_ids.add(trace_id)
            if service == "image-provider" and kind == "server":
                backend_ids["image_asset"].add(trace_id)
            if service == "cart" and kind == "server":
                backend_ids["cart_view"].add(trace_id)
            if kind != "server":
                continue
            minute = trace_minutes.get(trace_id)
            if minute is None:
                continue
            for ref in span.get("references") or []:
                parent = by_identity.get((ref.get("traceID"), ref.get("spanID")))
                if not parent or (parent.get("tag") or {}).get("span@kind") != "client":
                    continue
                parent_service = parent["process"]["serviceName"]
                if parent_service == "frontend-proxy" and service == "image-provider":
                    per_minute[minute]["image_edge_spans"] += 1
                if parent_service == "frontend" and service == "cart":
                    per_minute[minute]["cart_edge_spans"] += 1
                if parent_service == "frontend" and service == "recommendation":
                    tags = {item.get("key"): item.get("value") for item in parent[
                        "process"].get("tags") or []}
                    if tags.get("k8s.pod.uid") == schedule["frontend_worker1_uid"]:
                        per_minute[minute]["w1_recommendation_client_spans"] += 1
                        per_minute[minute]["w1_recommendation_client_ms"].append(parent["duration"] / 1000)
                break
    missing_proxy = sorted(issued_ids - proxy_ids)
    controls = {stream: {row["trace_id"] for row in requests if row[
        "stream"] == stream and row.get("trace_id")} for stream in ("image_asset", "cart_view")}
    missing_control_backend = {stream: sorted(ids - backend_ids[stream])
                               for stream, ids in controls.items()}
    missing_cart_frontend = sorted(controls["cart_view"] - frontend_ids)
    for trace_id in proxy_ids:
        minute = trace_minutes.get(trace_id)
        if minute is not None:
            per_minute[minute]["proxy_matched"] += 1
    for bucket in per_minute:
        for name in ("image_client_ms", "cart_client_ms", "w1_recommendation_client_ms"):
            values = bucket.pop(name)
            bucket[name.replace("_ms", "_latency_ms")] = {"n": len(values),
                "p50": percentile(values, .5), "p90": percentile(values, .9),
                "p95": percentile(values, .95)}
    return {"issued_requests": len(issued_ids), "all_request_rows": len(requests),
        "successful_requests": sum(bool(row.get("success")) for row in requests),
        "failed_request_rows": sum(not bool(row.get("success")) for row in requests),
        "proxy_server_matched": len(issued_ids & proxy_ids),
        "missing_proxy_trace_ids": missing_proxy,
        "missing_control_backend": missing_control_backend,
        "missing_cart_frontend": missing_cart_frontend,
        "uid_counts": {name: dict(value) for name, value in uid_counts.items()},
        "per_minute": per_minute, "request_window_errors": errors,
        "completion_crossed_window_end_count": completion_cross_window}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--stage", choices=("first", "late"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if socket.gethostname() != "k8s-worker2" or args.output.exists() or not str(
            args.output).startswith(str(ROOT)):
        raise ValueError("Worker2 data-disk capture only; output must be new")
    schedule = read(args.run / "schedule.json")
    if schedule["id"] != RUN_ID or schedule["fault_injected"]:
        raise ValueError("Unregistered or faulted source")
    requests, ids = issued(args.run)
    end = datetime.fromisoformat(schedule["scheduled_end_utc"]).timestamp()
    delay = 120 if args.stage == "first" else 240
    time.sleep(max(0, end + delay - time.time()))
    args.output.mkdir(parents=True)
    start = datetime.fromisoformat(schedule["scheduled_start_utc"]).timestamp()
    start_us, end_us = int(start * 1_000_000) - 1_000_000, int(end * 1_000_000) + 5_000_000
    index = "jaeger-jaeger-span-" + datetime.fromtimestamp(end, timezone.utc).strftime("%Y-%m-%d")
    spans, identities, source = capture(index, ids, start_us, end_us, args.output)
    summary = summarize(requests, spans, schedule)
    result = {"status": "TRACE_CAPTURE_COMPLETE" if source["actual_hits"] == source[
        "expected_hits"] else "TRACE_CAPTURE_INCOMPLETE",
        "stage": args.stage, "run_id": RUN_ID,
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "scheduled_end_utc": schedule["scheduled_end_utc"],
        "start_after_end_seconds": (datetime.fromisoformat(source[
            "read_started_utc"]).timestamp() - end),
        "read_duration_seconds": datetime.fromisoformat(source[
            "read_completed_utc"]).timestamp() - datetime.fromisoformat(source[
            "read_started_utc"]).timestamp(),
        "source": source, "issued_session_sha256": sha(args.run / "state-machine/sessions.jsonl"),
        "issued_control_sha256": {name: sha(args.run / "controls" / (name + ".jsonl"))
                                  for name in ("image_asset", "cart_view")},
        "span_identity_sha256": hashlib.sha256(json.dumps(sorted(identities),
            separators=(",", ":")).encode()).hexdigest(),
        "summary": summary, "private_gt_read": False}
    (args.output / "capture-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "stage": args.stage,
        "issued": summary["issued_requests"], "proxy_matched": summary["proxy_server_matched"],
        "missing_proxy": len(summary["missing_proxy_trace_ids"]),
        "raw_pages": len(source["raw_pages"]), "start_after_end_seconds": result[
            "start_after_end_seconds"]}))


if __name__ == "__main__":
    main()
