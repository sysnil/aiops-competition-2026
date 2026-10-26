#!/usr/bin/env python3
"""Archive raw proxy failures and nearby access records from OpenSearch."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import urllib.request


OPENSEARCH = "http://10.111.114.255:9200"


def query(start, end, *, size=1000, services=("frontend-proxy",)):
    body = {"size": size, "sort": [{"@timestamp": {"order": "asc"}}],
            "query": {"bool": {"filter": [
                {"range": {"@timestamp": {
                    "gte": start.isoformat(), "lt": end.isoformat()}}},
                {"terms": {"resource.service.name.keyword": list(services)}}]}}}
    request = urllib.request.Request(OPENSEARCH + "/otel-logs-*/_search",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def traces(trace_ids):
    if not trace_ids:
        return {"hits": {"hits": []}}
    body = {"size": 1000, "query": {"terms": {"traceID": trace_ids}}}
    request = urllib.request.Request(OPENSEARCH + "/jaeger-jaeger-span-*/_search",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.load(response)
    if len(value.get("hits", {}).get("hits", [])) == 1000:
        raise ValueError("Raw failure trace query hit its cap")
    return value


def dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def write(path, value):
    data = (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode()
    with path.open("xb") as output:
        output.write(data)
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start, end = dt(args.start), dt(args.end)
    if (start.tzinfo is None or end.tzinfo is None or end <= start or
            (end - start).total_seconds() > 1800 or args.output.exists()):
        raise ValueError("Bounded new diagnostic window required")
    args.output.mkdir(parents=True)
    # Find failures by raw access-log body; no model/GT data participates.
    failures = []
    cursor = start
    while cursor < end:
        next_cursor = min(cursor + timedelta(seconds=30), end)
        response = query(cursor, next_cursor)
        hits = response["hits"]["hits"]
        if len(hits) == 1000:
            raise ValueError("Diagnostic log query hit its page cap")
        failures.extend(hit for hit in hits if
            hit["_source"].get("resource", {}).get("service.name") == "frontend-proxy"
            and " 503 UC " in hit["_source"].get("body", ""))
        cursor = next_cursor
    context = {}
    for hit in failures:
        stamp = dt(hit["_source"]["@timestamp"])
        response = query(stamp - timedelta(seconds=2), stamp + timedelta(seconds=2),
                         services=("frontend-proxy", "frontend", "product-catalog"))
        context[hit["_source"].get("traceId", hit["_id"])] = response["hits"]["hits"]
    hashes = {"failures": write(args.output / "raw-503-access-hits.json", failures),
              "context": write(args.output / "nearby-access-and-app-logs.json", context)}
    trace_ids = [hit["_source"].get("traceId") for hit in failures]
    raw_traces = traces([value for value in trace_ids if value])
    hashes["traces"] = write(args.output / "raw-503-jaeger-spans.json", raw_traces)
    result = {"status": "DIAGNOSTICS_CAPTURED", "start": start.isoformat(),
              "end": end.isoformat(), "proxy_503_uc_count": len(failures),
              "failure_trace_ids": trace_ids,
              "failure_span_count": len(raw_traces["hits"]["hits"]),
              "sha256": hashes}
    write(args.output / "manifest.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
