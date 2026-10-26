"""Classify issued Canary requests lacking an archived proxy Server Span."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request


OPENSEARCH = "http://10.111.114.255:9200"


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def query(path, body=None):
    request = urllib.request.Request(OPENSEARCH + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    audit_path = args.run / "trace-audit/audit.json"
    sessions_path = args.run / "load/sessions.jsonl"
    audit = read(audit_path)
    if audit["status"] != "UID_CANARY_FAILED":
        raise ValueError("Unexpected Canary status")
    issued = {}
    with sessions_path.open() as stream:
        for line in stream:
            if line.strip():
                session = json.loads(line)
                for step in session["steps"]:
                    issued[step["trace_id"]] = {"operation": step["operation"],
                        "path": step["path"], "time_utc": step["time_utc"],
                        "latency_ms": step["latency_ms"],
                        "status_code": step["status_code"],
                        "session_index": session["index"]}
    stored = defaultdict(list)
    for relative, expected in audit["source"]["pages"].items():
        path = args.run / "trace-audit" / relative
        if sha(path) != expected:
            raise ValueError("Frozen Canary raw page hash mismatch")
        for hit in read(path)["hits"]["hits"]:
            source = hit["_source"]
            if source["traceID"] in issued:
                stored[source["traceID"]].append(source)
    missing = [trace for trace in issued if not any(span["process"][
        "serviceName"] == "frontend-proxy" and (span.get("tag") or {}).get(
            "span@kind") == "server" for span in stored[trace])]
    index = audit["source"]["index"]
    settings = query("/" + index + "/_settings")
    if settings[index]["settings"]["index"]["uuid"] != audit[
            "source"]["index_uuid"]:
        raise ValueError("Jaeger index UUID changed since Canary capture")
    body = {"size": 5000, "query": {"terms": {"traceID": sorted(missing)}}}
    response = query("/" + index + "/_search", body)
    total = response["hits"]["total"]
    if (response["_shards"].get("failed") or total.get("relation") != "eq" or
            total["value"] > 5000):
        raise RuntimeError("Missing-Trace exact-ID query incomplete")
    current = defaultdict(list)
    for hit in response["hits"]["hits"]:
        source = hit["_source"]
        if source["traceID"] in missing:
            current[source["traceID"]].append(source)
    results = []
    for trace in missing:
        proxy = [span for span in current[trace] if span["process"][
            "serviceName"] == "frontend-proxy" and (span.get("tag") or {}).get(
                "span@kind") == "server"]
        if proxy:
            if any(audit["query_start_us"] <= span["startTime"] < audit[
                    "query_end_us"] for span in proxy):
                category = "PROXY_SPAN_STORED_AFTER_FROZEN_CANARY_READ"
            else:
                category = "PROXY_SPAN_OUTSIDE_CANARY_QUERY_WINDOW"
        elif current[trace]:
            category = "OTHER_SPANS_PRESENT_PROXY_NOT_EXPORTED"
        else:
            category = "NO_CURRENT_STORAGE_SPANS"
        results.append({"trace_id": trace, **issued[trace],
            "archived_span_count": len(stored[trace]),
            "current_storage_span_count": len(current[trace]),
            "current_proxy_server_span_count": len(proxy),
            "proxy_start_times_us": [span["startTime"] for span in proxy],
            "current_services": dict(Counter(span["process"]["serviceName"]
                                             for span in current[trace])),
            "classification": category})
    output = {"status": "CANARY_PROXY_TRACE_GAP_AUDITED",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": read(args.run / "schedule.json")["id"],
        "original_canary_status_unchanged": "UID_CANARY_FAILED",
        "issued_requests": len(issued),
        "missing_proxy_trace_ids": len(missing),
        "categories": dict(Counter(item["classification"] for item in results)),
        "jaeger_index_uuid": audit["source"]["index_uuid"],
        "current_storage_query_total_hits": total["value"],
        "source_sha256": {str(path): sha(path) for path in
                          (audit_path, sessions_path)},
        "raw_page_hashes_verified": len(audit["source"]["pages"]),
        "results": results, "private_gt_read": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(output, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": output["status"], "missing": len(missing),
                      "categories": output["categories"]}))


if __name__ == "__main__":
    main()
