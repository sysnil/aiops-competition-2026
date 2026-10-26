#!/usr/bin/env python3
"""Count actual frontend-to-recommendation UID pairs from raw Jaeger spans."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def uid(span):
    return next((row.get("value") for row in span.get("process", {}).get("tags", [])
                 if row.get("key") == "k8s.pod.uid"), None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite routing evidence")
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00")).timestamp()
    manifest = json.loads((args.capture / "capture-manifest.json").read_text())
    traces = defaultdict(dict)
    page_count = 0
    for path in sorted((args.capture / "trace-storage-pages").glob("page-*.json")):
        relative = str(path.relative_to(args.capture))
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"][relative]:
            raise ValueError("Raw trace page changed after capture")
        for hit in json.loads(path.read_text())["hits"]["hits"]:
            span = hit["_source"]
            traces[span["traceID"]][span["spanID"]] = span
        page_count += 1
    pairs = defaultdict(lambda: defaultdict(set))
    unknown = Counter()
    for trace_id, spans in traces.items():
        for span in spans.values():
            if (span.get("process", {}).get("serviceName") != "recommendation" or
                    span.get("tag", {}).get("span@kind") != "server"):
                continue
            minute = int((span["startTimeMillis"] / 1000 - start) // 60)
            if minute < 0 or minute >= 10:
                continue
            dst = uid(span)
            parents = [spans.get(ref["spanID"]) for ref in span.get("references", [])]
            parents = [p for p in parents if p and
                       p.get("process", {}).get("serviceName") == "frontend"]
            if len(parents) != 1 or not uid(parents[0]) or not dst:
                unknown["missing_or_ambiguous_uid_parent"] += 1
                continue
            pairs[uid(parents[0]) + "->" + dst][minute].add(trace_id)
    result = {"status": "RAW_UID_ROUTING_AUDIT", "trace_source":
        manifest["trace_source"], "capture_manifest_sha256": hashlib.sha256(
        (args.capture / "capture-manifest.json").read_bytes()).hexdigest(),
        "page_count": page_count, "pair_trace_ids_per_minute": {
            pair: [len(bins[m]) for m in range(10)] for pair, bins in pairs.items()},
        "unattributed": dict(unknown)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
