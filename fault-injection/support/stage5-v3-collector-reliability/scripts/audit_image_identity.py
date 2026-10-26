#!/usr/bin/env python3
"""Audit a predetermined ordinary Image request's raw Span Pod identity."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request


OPENSEARCH = "http://10.111.114.255:9200"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-requests", type=Path, required=True)
    parser.add_argument("--topology", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.raw_output.exists():
        raise ValueError("Refusing overwrite")
    with args.control_requests.open() as source:
        first = json.loads(source.readline())
    if first["index"] != 0 or first["operation"] != "image_asset" or not first[
            "success"]:
        raise ValueError("Predetermined first Image control request invalid")
    topology = json.loads(args.topology.read_text())
    query = {"size": 100, "query": {"term": {"traceID": first["trace_id"]}}}
    request = urllib.request.Request(OPENSEARCH + "/jaeger-jaeger-span-2026-10-12/_search",
        data=json.dumps(query).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = json.load(response)
    if raw["_shards"].get("failed") or raw["hits"]["total"].get("relation") != "eq" or raw[
            "hits"]["total"]["value"] > 100:
        raise RuntimeError("Exact Image Trace query incomplete")
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_text(json.dumps(raw, indent=2) + "\n")
    matches = [hit["_source"] for hit in raw["hits"]["hits"] if hit["_source"][
        "process"]["serviceName"] == "image-provider" and (hit["_source"].get(
            "tag") or {}).get("span@kind") == "server"]
    if len(matches) != 1:
        raise RuntimeError("Image Server Span not unique")
    tags = {item.get("key"): item.get("value") for item in matches[0][
        "process"].get("tags") or []}
    pods = [item for item in topology["service_pods"].get("image-provider", [])
            if item["ready"]]
    endpoints = topology["endpoint_slices"].get("image-provider", [])
    inferred = pods[0] if len(pods) == 1 and any(row.get("pod_uid") == pods[0][
        "uid"] and row.get("ready") for row in endpoints) else None
    direct = tags.get("k8s.pod.uid")
    status = "DIRECT_UID_VERIFIED" if direct and inferred and direct == inferred["uid"] else \
        "PUBLIC_TOPOLOGY_INFERRED_SINGLE_READY_POD" if not direct and inferred else \
        "UNATTRIBUTED_OR_UID_MISMATCH"
    value = {"status": status, "at_utc": datetime.now(timezone.utc).isoformat(),
        "selection": "first preregistered image control request index 0, no outcome-based choice",
        "trace_id": first["trace_id"], "span_id": matches[0]["spanID"],
        "direct_pod_uid": direct, "direct_container_id": tags.get("container.id"),
        "topology_pod": inferred, "server_span_resource_tag_keys": sorted(tags),
        "control_request_sha256": sha(args.control_requests),
        "raw_opensearch_response_sha256": sha(args.raw_output),
        "topology_sha256": sha(args.topology), "private_gt_read": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": status, "trace_id": first["trace_id"],
                      "direct_uid": direct, "inferred_uid": inferred["uid"] if inferred else None}))


if __name__ == "__main__":
    main()
