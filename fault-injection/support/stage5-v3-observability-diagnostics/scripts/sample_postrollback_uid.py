"""Read-only fixed-window sample of Server Span UID after Canary rollback."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request


OPENSEARCH = "http://10.111.114.255:9200"
INDEX = "jaeger-jaeger-span-2026-10-12"
SERVICES = ("cart", "checkout", "shipping", "quote")
START = "2026-10-12T11:03:00+00:00"
END = "2026-10-12T11:05:00+00:00"


def request(path, body=None):
    query = urllib.request.Request(OPENSEARCH + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(query, timeout=20) as response:
        return json.load(response)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    pods = json.loads(subprocess.check_output(["kubectl", "-n", "otel-demo",
        "get", "pods", "-o", "json"], text=True, timeout=20))["items"]
    by_service = {}
    for service in SERVICES:
        matching = [pod for pod in pods if pod["metadata"]["labels"].get(
            "opentelemetry.io/name") == service and pod["status"].get(
                "phase") == "Running" and not pod["metadata"].get(
                    "deletionTimestamp")]
        if len(matching) != 1:
            raise ValueError(service + " is not uniquely placed")
        by_service[service] = {"uid": matching[0]["metadata"]["uid"],
            "node": matching[0]["spec"].get("nodeName"),
            "name": matching[0]["metadata"]["name"]}
    settings = request("/" + INDEX + "/_settings")
    index_uuid = settings[INDEX]["settings"]["index"]["uuid"]
    first = int(datetime.fromisoformat(START).timestamp() * 1_000_000)
    last = int(datetime.fromisoformat(END).timestamp() * 1_000_000)
    samples = {}
    args.output.mkdir(parents=True)
    for service in SERVICES:
        query = {"size": 3000, "query": {"bool": {"filter": [
            {"range": {"startTime": {"gte": first, "lt": last}}},
            {"term": {"process.serviceName": service}}]}}}
        response = request("/" + INDEX + "/_search", query)
        total = response["hits"]["total"]
        if (response["_shards"].get("failed") or total.get("relation") !=
                "eq" or total["value"] >= 3000):
            raise RuntimeError("Fixed-window UID query incomplete")
        source = args.output / (service + "-raw.json")
        with source.open("x") as stream:
            json.dump(response, stream)
            stream.write("\n")
        spans = [hit["_source"] for hit in response["hits"]["hits"]
                 if (hit["_source"].get("tag") or {}).get("span@kind") == "server"]
        observed = Counter()
        examples = []
        for span in spans:
            tags = {item.get("key"): item.get("value") for item in span[
                "process"].get("tags") or []}
            uid = tags.get("k8s.pod.uid")
            observed["uid_matches_actual" if uid == by_service[service]["uid"]
                else "uid_missing" if uid is None else "uid_mismatch"] += 1
            if len(examples) < 2:
                examples.append({"trace_id": span["traceID"],
                    "span_id": span["spanID"], "resource_uid": uid,
                    "operation": span["operationName"]})
        samples[service] = {"source_file_sha256": sha(source),
            "source_total_spans": total["value"],
            "server_spans": len(spans), "uid_categories": dict(observed),
            "actual_pod": by_service[service], "examples": examples}
    result = {"status": "POST_ROLLBACK_RAW_UID_SAMPLE",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "query_start_utc": START, "query_end_utc": END,
        "trace_source": "JAEGER_OPENSEARCH_STORAGE",
        "index": INDEX, "index_uuid": index_uuid,
        "services": samples, "private_gt_read": False}
    with (args.output / "summary.json").open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"],
        "server_spans": {name: row["server_spans"] for name, row in samples.items()},
        "uid_missing": {name: row["uid_categories"].get("uid_missing", 0)
                        for name, row in samples.items()}}))


if __name__ == "__main__":
    main()
