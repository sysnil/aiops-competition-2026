"""Audit ordinary-business Pod UID in Jaeger's OpenSearch span documents."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request

import yaml


OPENSEARCH = "http://10.111.114.255:9200"
SERVICES = {"frontend-proxy", "frontend", "cart", "checkout", "shipping",
            "quote"}
TARGETS = ("cart", "checkout", "shipping", "quote")


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def request_json(path, body=None):
    request = urllib.request.Request(OPENSEARCH + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def issued_requests(path):
    traces = set()
    attempts = successes = 0
    with path.open() as source:
        for line in source:
            if not line.strip():
                continue
            session = json.loads(line)
            for step in session["steps"]:
                attempts += 1
                successes += bool(step.get("success"))
                if not step.get("trace_id") or step["trace_id"] in traces:
                    raise ValueError("Missing or duplicated issued Trace ID")
                traces.add(step["trace_id"])
    return traces, attempts, successes


def pod_identifications():
    raw = subprocess.check_output(["kubectl", "-n", "otel-demo", "get", "pods",
        "-o", "json"], text=True, timeout=20)
    pods = json.loads(raw)["items"]
    result = {}
    for service in TARGETS:
        matches = [pod for pod in pods if pod["metadata"]["labels"].get(
            "opentelemetry.io/name") == service and pod["metadata"].get(
                "deletionTimestamp") is None and pod["status"].get(
                    "phase") == "Running" and pod["status"].get(
                        "containerStatuses") and all(row.get("ready") for row
                            in pod["status"]["containerStatuses"])]
        if len(matches) != 1:
            raise RuntimeError(service + " does not have one Ready Pod")
        pod = matches[0]
        result[service] = {"uid": pod["metadata"]["uid"],
            "name": pod["metadata"]["name"],
            "node": pod["spec"].get("nodeName")}
    return result, raw


def capture(index, start_us, end_us, output):
    settings = request_json("/" + index + "/_settings")
    mappings = request_json("/" + index + "/_mapping")
    uuid = settings[index]["settings"]["index"]["uuid"]
    query = {"size": 2000, "sort": ["_doc"], "query": {"bool": {
        "filter": [{"range": {"startTime": {"gte": start_us, "lt": end_us}}},
                   {"terms": {"process.serviceName": sorted(SERVICES)}}]}}}
    response = request_json("/" + index + "/_search?scroll=2m", query)
    expected = response["hits"]["total"]
    if expected.get("relation") != "eq" or not 1 <= expected["value"] <= 120000:
        raise ValueError("Jaeger read did not return a bounded exact total")
    entries = {}
    hashes = {}
    scroll_id = response.get("_scroll_id")
    try:
        for number in range(60):
            if response["_shards"].get("failed"):
                raise RuntimeError("OpenSearch query has failed shards")
            page_path = output / "raw-pages" / ("page-%04d.json" % number)
            page_path.parent.mkdir(parents=True, exist_ok=True)
            with page_path.open("x") as stream:
                json.dump(response, stream)
                stream.write("\n")
            hashes[str(page_path.relative_to(output))] = sha(page_path)
            for hit in response["hits"]["hits"]:
                source = hit["_source"]
                identity = (source["traceID"], source["spanID"])
                if identity in entries:
                    raise ValueError("Duplicate raw trace/span identity")
                entries[identity] = source
            if len(entries) == expected["value"]:
                break
            if len(entries) > expected["value"] or not scroll_id:
                raise RuntimeError("Jaeger scroll count mismatch")
            response = request_json("/_search/scroll", {"scroll": "2m",
                                                          "scroll_id": scroll_id})
            scroll_id = response.get("_scroll_id", scroll_id)
        else:
            raise RuntimeError("Jaeger scroll page budget exceeded")
    finally:
        if scroll_id:
            try:
                request_json("/_search/scroll", {"scroll_id": [scroll_id]})
            except Exception:
                pass
    return entries, {"index": index, "index_uuid": uuid,
        "mapping_sha256": digest_json(mappings[index]["mappings"]),
        "query_sha256": digest_json(query),
        "exact_total": expected["value"], "pages": hashes,
        "capture_at_utc": datetime.now(timezone.utc).isoformat()}


def assess(entries, issued, pods, min_count):
    by_service = {service: Counter() for service in TARGETS}
    examples = {service: [] for service in TARGETS}
    proxy_seen = set()
    target_span_keys = set()
    for identity, span in entries.items():
        trace_id = span["traceID"]
        if trace_id not in issued:
            continue
        service = span["process"]["serviceName"]
        kind = (span.get("tag") or {}).get("span@kind")
        if service == "frontend-proxy" and kind == "server":
            proxy_seen.add(trace_id)
        if service not in TARGETS or kind != "server":
            continue
        target_span_keys.add(identity)
        row = by_service[service]
        row["issued_server_spans"] += 1
        tags = {item.get("key"): item.get("value") for item in span[
            "process"].get("tags") or []}
        uid = tags.get("k8s.pod.uid")
        if uid == pods[service]["uid"] and tags.get("k8s.pod.name") == pods[
                service]["name"]:
            row["UID_PRESENT_AND_VERIFIED"] += 1
        elif uid:
            row["UID_MISMATCH"] += 1
        else:
            row["UID_MISSING"] += 1
        if len(examples[service]) < 3:
            examples[service].append({"trace_id": trace_id,
                "span_id": span["spanID"], "operation": span["operationName"],
                "resource_uid": uid, "resource_pod_name": tags.get("k8s.pod.name"),
                "actual_pod_uid": pods[service]["uid"]})
    matched_edges = Counter()
    for identity in target_span_keys:
        span = entries[identity]
        for ref in span.get("references") or []:
            parent = entries.get((ref.get("traceID"), ref.get("spanID")))
            if parent and (parent.get("tag") or {}).get("span@kind") == "client":
                matched_edges[parent["process"]["serviceName"] + "->" +
                              span["process"]["serviceName"]] += 1
                break
    checks = {"ordinary_client_errors_zero": True,
        "every_issued_trace_has_proxy_server_span": len(proxy_seen) == len(issued),
        "each_target_minimum_issued_server_spans": all(by_service[service][
            "issued_server_spans"] >= min_count for service in TARGETS),
        "each_target_direct_uid_matches_pod": all(by_service[service][
            "UID_PRESENT_AND_VERIFIED"] == by_service[service]["issued_server_spans"]
            and by_service[service]["UID_MISMATCH"] == 0 and by_service[service][
                "UID_MISSING"] == 0 for service in TARGETS)}
    return {"checks": checks, "target_service_counts": {service: dict(count)
        for service, count in by_service.items()}, "trace_examples": examples,
        "issued_proxy_trace_ids_matched": len(proxy_seen),
        "issued_trace_ids": len(issued),
        "matched_client_server_edges": dict(matched_edges)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite UID audit")
    prereg = yaml.safe_load(args.prereg.read_text())
    schedule = read(args.run / "schedule.json")
    workload = read(args.run / "result.json")
    if (schedule["id"] != prereg["canary"]["id"] or workload["status"] !=
            "CANARY_WORKLOAD_COMPLETED" or schedule["fault_injected"]):
        raise ValueError("Canary workload did not finish blind and fault-free")
    if sha(Path(__file__)) != prereg["source_sha256"]["audit_uid_canary_py"]:
        raise ValueError("Canary audit code drift")
    end = datetime.fromisoformat(schedule["scheduled_end_utc"]).timestamp()
    time.sleep(max(0, end + prereg["canary"][
        "jaeger_read_not_before_end_plus_seconds"] - time.time()))
    start_us = int(datetime.fromisoformat(schedule[
        "scheduled_start_utc"]).timestamp() * 1_000_000) - int(prereg[
            "canary"]["jaeger_query_before_start_padding_seconds"] * 1_000_000)
    end_us = int(end * 1_000_000) + int(prereg["canary"][
        "jaeger_query_after_end_padding_seconds"] * 1_000_000)
    issued, attempts, successes = issued_requests(args.run /
                                                  "load/sessions.jsonl")
    if attempts != successes:
        raise ValueError("Ordinary business Canary had client failures")
    index = "jaeger-jaeger-span-" + datetime.fromtimestamp(end,
                                                 timezone.utc).strftime("%Y-%m-%d")
    args.output.mkdir(parents=True)
    spans, source = capture(index, start_us, end_us, args.output)
    pods, pod_snapshot = pod_identifications()
    with (args.output / "kubernetes-pods.json").open("x") as stream:
        stream.write(pod_snapshot)
        if not pod_snapshot.endswith("\n"):
            stream.write("\n")
    result = assess(spans, issued, pods, prereg["canary"][
        "each_target_service_issued_trace_server_span_min"])
    result.update({"status": "UID_CANARY_PASS" if all(result["checks"].values())
        else "UID_CANARY_FAILED", "source": source,
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "scheduled_start_utc": schedule["scheduled_start_utc"],
        "scheduled_end_utc": schedule["scheduled_end_utc"],
        "query_start_us": start_us, "query_end_us": end_us,
        "ordinary_requests": attempts,
        "ordinary_successes": successes,
        "actual_pods": pods,
        "pod_snapshot_sha256": sha(args.output / "kubernetes-pods.json"),
        "sessions_sha256": sha(args.run / "load/sessions.jsonl"),
        "schedule_sha256": sha(args.run / "schedule.json"),
        "prereg_sha256": sha(args.prereg),
        "audit_code_sha256": sha(Path(__file__)),
        "private_gt_read": False})
    with (args.output / "audit.json").open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"],
        "ordinary_requests": attempts, "proxy_trace_ids": result[
        "issued_proxy_trace_ids_matched"],
        "target_counts": result["target_service_counts"]}))
    if result["status"] != "UID_CANARY_PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
