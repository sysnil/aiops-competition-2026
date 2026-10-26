#!/usr/bin/env python3
"""Audit every issued Canary request against immutable Jaeger storage reads."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request

import yaml


OPENSEARCH = "http://10.111.114.255:9200"
PROMETHEUS = "http://10.97.14.138:9090"
SERVICES = ["frontend-proxy", "frontend", "image-provider", "cart", "checkout",
            "shipping", "quote", "recommendation"]
UID_TARGETS = ["cart", "checkout", "shipping", "quote"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def request_json(url, body=None):
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def issued(run):
    rows = []
    with (run / "state-machine/sessions.jsonl").open() as source:
        for line in source:
            session = json.loads(line)
            rows.extend({"stream": "state-machine", "session_index": session["index"], **step}
                        for step in session["steps"])
    for name in ("image_asset", "cart_view"):
        with (run / "controls" / (name + ".jsonl")).open() as source:
            rows.extend({"stream": name, **json.loads(line)} for line in source)
    valid = [row for row in rows if row.get("trace_id")]
    ids = [row["trace_id"] for row in valid]
    if len(ids) != len(set(ids)):
        raise ValueError("Issued Trace IDs are not unique")
    return rows, valid


def capture(index, ids, output, start_us, end_us):
    settings = request_json(f"{OPENSEARCH}/{index}/_settings")
    uuid = settings[index]["settings"]["index"]["uuid"]
    query = {"size": 2000, "sort": ["_doc"], "query": {"bool": {"filter": [
        {"terms": {"traceID": ids}}, {"terms": {"process.serviceName": SERVICES}},
        {"range": {"startTime": {"gte": start_us, "lt": end_us}}}]}}}
    response = request_json(f"{OPENSEARCH}/{index}/_search?scroll=2m", query)
    total = response["hits"]["total"]
    if total.get("relation") != "eq" or total["value"] > 120000:
        raise RuntimeError("Trace capture total is not bounded and exact")
    stored = {}
    hashes = {}
    scroll_id = response.get("_scroll_id")
    try:
        for number in range(60):
            if response["_shards"].get("failed"):
                raise RuntimeError("OpenSearch shard failure")
            page = output / "raw-pages" / f"page-{number:04}.json"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(json.dumps(response) + "\n")
            hashes[str(page.relative_to(output))] = sha(page)
            for hit in response["hits"]["hits"]:
                span = hit["_source"]
                key = (span["traceID"], span["spanID"])
                if key in stored:
                    raise ValueError("Duplicate Trace/Span identity")
                stored[key] = span
            if len(stored) == total["value"]:
                break
            if len(stored) > total["value"] or not scroll_id:
                raise RuntimeError("Trace scroll incomplete")
            response = request_json(f"{OPENSEARCH}/_search/scroll", {"scroll": "2m", "scroll_id": scroll_id})
            scroll_id = response.get("_scroll_id", scroll_id)
        else:
            raise RuntimeError("Trace scroll page budget exhausted")
    finally:
        if scroll_id:
            try:
                request_json(f"{OPENSEARCH}/_search/scroll", {"scroll_id": [scroll_id]})
            except Exception:
                pass
    return stored, {"index": index, "index_uuid": uuid,
                    "query_start_us": start_us, "query_end_us": end_us,
                    "query_sha256": hashlib.sha256(
        json.dumps(query, sort_keys=True).encode()).hexdigest(), "exact_total": total["value"], "pages": hashes}


def pod_snapshot():
    import subprocess
    source = subprocess.check_output(["kubectl", "-n", "otel-demo", "get", "pods", "-o", "json"],
                                     text=True, timeout=25)
    pods = read_json(source)["items"]
    mapping = {pod["metadata"]["uid"]: {"name": pod["metadata"]["name"],
        "service": pod["metadata"]["labels"].get("opentelemetry.io/name"),
        "node": pod["spec"].get("nodeName"),
        "ready": pod["status"].get("phase") == "Running" and all(
            row.get("ready") for row in pod["status"].get("containerStatuses", []))}
        for pod in pods}
    return source, mapping


def read_json(value):
    return json.loads(value)


def resource(span):
    return {item.get("key"): item.get("value") for item in span.get("process", {}).get("tags") or []}


def metric_delta(query, start, end):
    values = []
    for timestamp in (start, end):
        params = urllib.parse.urlencode({"query": query, "time": timestamp})
        response = request_json(f"{PROMETHEUS}/api/v1/query?{params}")
        rows = response["data"]["result"]
        values.append(float(rows[0]["value"][1]) if len(rows) == 1 else None)
    return {"start": values[0], "end": values[1],
            "delta": values[1] - values[0] if None not in values else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    prereg = yaml.safe_load(args.prereg.read_text())
    if prereg.get("auditor_sha256") and prereg["auditor_sha256"] != sha(Path(__file__)):
        raise ValueError("Canary auditor changed after preregistration")
    schedule = read(args.run / "schedule.json")
    run_result = read(args.run / "result.json")
    if run_result["status"] != "CANARY_WORKLOAD_COMPLETE" or schedule["fault_injected"]:
        raise ValueError("Canary workload incomplete or not no-fault")
    if schedule["source_sha256"][str(args.prereg)] != sha(args.prereg):
        raise ValueError("Preregistration changed during Canary")
    end = datetime.fromisoformat(schedule["scheduled_end_utc"]).timestamp()
    time.sleep(max(0, end + prereg["quality_gate"]["read_not_before_end_plus_seconds"] - time.time()))
    start = datetime.fromisoformat(schedule["scheduled_start_utc"]).timestamp()
    start_us = int(start * 1_000_000) - 1_000_000
    end_us = int(end * 1_000_000) + 5_000_000
    rows, valid = issued(args.run)
    index = "jaeger-jaeger-span-" + datetime.fromtimestamp(end, timezone.utc).strftime("%Y-%m-%d")
    args.output.mkdir(parents=True)
    spans, source = capture(index, [row["trace_id"] for row in valid], args.output,
                            start_us, end_us)
    pod_source, pods = pod_snapshot()
    (args.output / "kubernetes-pods.json").write_text(pod_source)
    by_trace = defaultdict(list)
    for span in spans.values():
        by_trace[span["traceID"]].append(span)
    proxy_seen = {trace_id for trace_id, matches in by_trace.items() if any(
        span["process"]["serviceName"] == "frontend-proxy" and
        (span.get("tag") or {}).get("span@kind") == "server" for span in matches)}
    initial_missing = sorted({row["trace_id"] for row in valid} - proxy_seen)
    late = None
    if initial_missing:
        time.sleep(max(0, end + prereg["quality_gate"]["repeat_missing_query_at_end_plus_seconds"] - time.time()))
        query = {"size": 10000, "query": {"bool": {"filter": [
            {"terms": {"traceID": initial_missing}},
            {"term": {"process.serviceName": "frontend-proxy"}},
            {"range": {"startTime": {"gte": start_us, "lt": end_us}}}]}}}
        late = request_json(f"{OPENSEARCH}/{index}/_search", query)
        if late["_shards"].get("failed") or late["hits"]["total"].get("relation") != "eq":
            raise RuntimeError("Late proxy exact-ID query incomplete")
        (args.output / "late-proxy-query.json").write_text(json.dumps(late) + "\n")
        for hit in late["hits"]["hits"]:
            span = hit["_source"]
            if (span.get("tag") or {}).get("span@kind") == "server":
                proxy_seen.add(span["traceID"])
    missing = sorted({row["trace_id"] for row in valid} - proxy_seen)
    direct = {service: Counter() for service in UID_TARGETS}
    edges = Counter()
    for key, span in spans.items():
        service = span["process"]["serviceName"]
        if service in direct and (span.get("tag") or {}).get("span@kind") == "server":
            counter = direct[service]
            counter["total_server_spans"] += 1
            tags = resource(span)
            uid = tags.get("k8s.pod.uid")
            pod = pods.get(uid)
            if uid and pod and pod["service"] == service and tags.get("k8s.pod.name") == pod["name"]:
                counter["DIRECT_UID_VERIFIED"] += 1
            elif uid:
                counter["UID_MISMATCH"] += 1
            else:
                counter["UNATTRIBUTED_DIRECT_UID"] += 1
        if (span.get("tag") or {}).get("span@kind") == "server":
            for reference in span.get("references") or []:
                parent = spans.get((reference.get("traceID"), reference.get("spanID")))
                if parent and (parent.get("tag") or {}).get("span@kind") == "client":
                    edges[parent["process"]["serviceName"] + "->" + service] += 1
                    break
    all_ids = {row["trace_id"] for row in valid}
    errors = [row for row in rows if not row.get("success")]
    control_ids = {row["trace_id"] for row in valid if row["stream"] != "state-machine"}
    control_missing = control_ids - proxy_seen
    gateway = run_result["collector_after"]["pod"]
    collector = {
        "grpc_refused_spans": metric_delta('sum(otelcol_receiver_refused_spans_total{k8s_pod_name="' + gateway + '",transport="grpc"})', start, end),
        "memory_refused_spans": metric_delta('sum(otelcol_processor_memory_limiter_refused_spans_total{k8s_pod_name="' + gateway + '"})', start, end),
        "memory_refused_logs": metric_delta('sum(otelcol_processor_memory_limiter_refused_log_records_total{k8s_pod_name="' + gateway + '"})', start, end),
    }
    limiter = collector["memory_refused_spans"]
    limiter_absent = limiter["start"] is None and limiter["end"] is None
    limiter_status = ("COUNTER_PRESENT_ZERO_DELTA" if limiter["delta"] == 0 else
        "SERIES_ABSENT_UNKNOWN_NOT_ZERO" if limiter_absent else
        "COUNTER_INCREASE_OR_PARTIAL_MISSING")
    image_ids = {row["trace_id"] for row in valid if row["stream"] == "image_asset"}
    cart_ids = {row["trace_id"] for row in valid if row["stream"] == "cart_view"}
    image_server_ids = {trace_id for trace_id, rows in by_trace.items() if any(
        span["process"]["serviceName"] == "image-provider" and
        (span.get("tag") or {}).get("span@kind") == "server" for span in rows)}
    cart_server_ids = {trace_id for trace_id, rows in by_trace.items() if any(
        span["process"]["serviceName"] == "cart" and
        (span.get("tag") or {}).get("span@kind") == "server" for span in rows)}
    frontend_server_ids = {trace_id for trace_id, rows in by_trace.items() if any(
        span["process"]["serviceName"] == "frontend" and
        (span.get("tag") or {}).get("span@kind") == "server" for span in rows)}
    checks = {"all_issued_have_trace_id": len(valid) == len(rows),
        "client_errors_zero": not errors, "proxy_server_100_percent": all_ids <= proxy_seen,
        "fixed_controls_100_percent": not control_missing,
        "client_server_frontend": edges["frontend-proxy->frontend"] > 0,
        "client_server_cart": edges["frontend->cart"] > 0,
        "fixed_image_backend_100_percent": image_ids <= image_server_ids,
        "fixed_cart_frontend_and_backend_100_percent": cart_ids <= (
            cart_server_ids & frontend_server_ids),
        "collector_limiter_evidence_acceptable_for_diagnostic_canary": (
            limiter["delta"] == 0 or (limiter_absent and prereg["quality_gate"].get(
                "collector_limiter_refused_absent_series") == "UNKNOWN_NOT_ZERO")),
        "collector_grpc_refused_zero": collector["grpc_refused_spans"]["delta"] == 0,
        "collector_pod_uid_stable_no_restart": schedule["collector_before"]["uid"] ==
            run_result["collector_after"]["uid"] and run_result["collector_after"]["restarts"] == 0,
        "envoy_spans_dropped_zero_delta": run_result["envoy_admin_stats_after"].get("tracing.opentelemetry.spans_dropped", 0) ==
            schedule["envoy_admin_stats_before"].get("tracing.opentelemetry.spans_dropped", 0)}
    if schedule["role"].startswith("collector_repair_"):
        checks["four_direct_uid_100_percent"] = all(direct[service]["total_server_spans"] >=
            prereg["quality_gate"]["uid_issued_server_spans_each_target_min"] and
            direct[service]["DIRECT_UID_VERIFIED"] == direct[service]["total_server_spans"]
            for service in UID_TARGETS)
    status = ("COLLECTOR_CANARY_FAIL" if not all(checks.values()) else
        "COLLECTOR_CANARY_COVERAGE_PASS_METRIC_UNKNOWN" if limiter_absent else
        "COLLECTOR_CANARY_PASS")
    result = {"status": status,
        "id": schedule["id"], "role": schedule["role"], "at_utc": datetime.now(timezone.utc).isoformat(),
        "checks": checks, "issued_requests": len(rows), "issued_with_trace_id": len(valid),
        "client_errors": len(errors), "proxy_server_matched": len(all_ids & proxy_seen),
        "proxy_server_fraction": len(all_ids & proxy_seen) / len(all_ids) if all_ids else None,
        "initial_missing_proxy_trace_ids": initial_missing,
        "missing_proxy_trace_ids": missing,
        "late_proxy_total_hits": late["hits"]["total"]["value"] if late else 0,
        "fixed_control_trace_count": len(control_ids),
        "fixed_control_missing_proxy_count": len(control_missing),
        "fixed_image_missing_backend_count": len(image_ids - image_server_ids),
        "fixed_cart_missing_frontend_or_backend_count": len(cart_ids - (cart_server_ids & frontend_server_ids)),
        "span_identity_count": len(spans),
        "span_identity_sha256": hashlib.sha256(json.dumps(sorted(spans), separators=(",", ":")).encode()).hexdigest(),
        "matched_client_server_edges": dict(edges),
        "uid_counts": {service: dict(counter) for service, counter in direct.items()},
        "collector_counters": collector,
        "limiter_refusal_metric_status": limiter_status,
        "limiter_refusal_absence_interpreted_as_zero": False,
        "envoy_admin_stats_before": schedule["envoy_admin_stats_before"],
        "envoy_admin_stats_after": run_result["envoy_admin_stats_after"],
        "source": source, "pod_snapshot_sha256": sha(args.output / "kubernetes-pods.json"),
        "audit_code_sha256": sha(Path(__file__)),
        "session_sha256": sha(args.run / "state-machine/sessions.jsonl"),
        "schedule_sha256": sha(args.run / "schedule.json"), "private_gt_read": False}
    (args.output / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("status", "issued_requests", "proxy_server_matched", "proxy_server_fraction", "missing_proxy_trace_ids", "collector_counters")}), flush=True)
    if result["status"] == "COLLECTOR_CANARY_FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
