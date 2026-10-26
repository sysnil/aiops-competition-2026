"""Read-only exact-ID audit of unresolved archived parent references."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import urllib.request


OPENSEARCH = "http://10.111.114.255:9200"
INDEX = "jaeger-jaeger-span-2026-10-12"
HEX = re.compile(r"^[0-9a-f]{16,32}$")


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def fetch(path, body=None):
    request = urllib.request.Request(OPENSEARCH + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def classify(parent, begin_us, end_us):
    if parent is None:
        return "UNKNOWN_PARENT_NOT_IN_CURRENT_STORAGE"
    stamp = parent["startTime"]
    if stamp < begin_us:
        return "VERIFIED_PARENT_BEFORE_CAPTURE_WINDOW"
    if stamp >= end_us:
        return "VERIFIED_PARENT_AFTER_CAPTURE_WINDOW"
    return "FOUND_ONLY_AFTER_IMMUTABLE_AUDIT"


def run(audits, schedules, sources):
    settings = fetch("/" + INDEX + "/_settings")
    current_uuid = settings[INDEX]["settings"]["index"]["uuid"]
    expected = {read(path)["indices"][INDEX]["uuid"] for path in sources}
    if expected != {current_uuid}:
        raise ValueError("Jaeger OpenSearch index UUID differs from archived source")
    output = []
    queried = {}
    for audit_path, schedule_path in zip(audits, schedules):
        audit = read(audit_path)
        schedule = read(schedule_path)
        begin = int(datetime.fromisoformat(schedule[
            "scheduled_start_utc"]).timestamp() * 1_000_000)
        end = int(datetime.fromisoformat(schedule[
            "scheduled_end_utc"]).timestamp() * 1_000_000)
        if audit["run_id"] != schedule["run_id"]:
            raise ValueError("Audit and schedule refer to different runs")
        for row in audit["unresolved_parent_examples"]:
            trace_id, parent_id = row["parent_trace_id"], row["parent_span_id"]
            if not HEX.fullmatch(trace_id) or not HEX.fullmatch(parent_id):
                raise ValueError("Invalid requested trace/span identity")
            if trace_id not in queried:
                query = {"size": 200, "query": {"term": {"traceID": trace_id}}}
                response = fetch("/" + INDEX + "/_search", query)
                total = response["hits"]["total"]
                if (response["_shards"].get("failed") != 0 or
                        total.get("relation") != "eq" or total["value"] > 200):
                    raise RuntimeError("Exact-ID storage query was incomplete")
                queried[trace_id] = {"query_sha256": digest_json(query),
                    "total": total["value"], "hits": response["hits"]["hits"]}
            results = [hit["_source"] for hit in queried[trace_id]["hits"]
                       if hit["_source"]["traceID"] == trace_id and
                       hit["_source"]["spanID"] == parent_id]
            if len(results) > 1:
                raise ValueError("Duplicate parent span in current storage")
            parent = results[0] if results else None
            output.append({"run_id": audit["run_id"], "child": row,
                "classification": classify(parent, begin, end),
                "parent_start_us": parent.get("startTime") if parent else None,
                "parent_service": parent.get("process", {}).get(
                    "serviceName") if parent else None,
                "parent_operation": parent.get("operationName") if parent else None,
                "parent_source_sha256": digest_json(parent) if parent else None,
                "parent_start_delta_from_window_us": (parent["startTime"] - begin)
                    if parent else None,
                "storage_query_sha256": queried[trace_id]["query_sha256"],
                "storage_trace_hits": queried[trace_id]["total"]})
    return {"status": "READ_ONLY_PARENT_BOUNDARY_AUDIT",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "trace_storage_source": "JAEGER_OPENSEARCH_STORAGE",
        "index": INDEX, "index_uuid": current_uuid,
        "query_count": len(queried), "unresolved_references_checked": len(output),
        "source_sha256": {str(path): sha(path) for path in
                          [*audits, *schedules, *sources]},
        "results": output, "private_gt_read": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, action="append", required=True)
    parser.add_argument("--schedule", type=Path, action="append", required=True)
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (len(args.audit) != len(args.schedule) or len(args.audit) !=
            len(args.source) or args.output.exists()):
        raise ValueError("Need paired immutable inputs and a new output")
    result = run(args.audit, args.schedule, args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"],
        "references": result["unresolved_references_checked"],
        "classes": {name: sum(row["classification"] == name for row in
            result["results"]) for name in sorted({row["classification"]
            for row in result["results"]})}}))


if __name__ == "__main__":
    main()
