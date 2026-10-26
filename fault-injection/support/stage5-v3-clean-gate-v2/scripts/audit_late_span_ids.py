"""Read-only ID comparison between immutable +60 archive and current Jaeger storage."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request


OS = "http://10.111.114.255:9200"


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def request(path, body=None, method=None):
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(OS + path, data=payload,
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def archived(archive_root, manifest, phase):
    prefix = f"capture-pipeline/trace-completion/{phase}/"
    root = archive_root / prefix
    identities, service_by_span, edges = set(), {}, set()
    paths = sorted((root / "traces").glob("storage-*.json"))
    if not paths:
        raise RuntimeError("Missing immutable +60 trace archive")
    for path in paths:
        relative = str(path.relative_to(archive_root))
        if sha(path) != manifest["files"][relative]["sha256"]:
            raise RuntimeError("Archived +60 page hash mismatch")
        for trace in json.loads(path.read_text())["data"]:
            local_services = {span["spanID"].lower(): trace["processes"][
                span["processID"]]["serviceName"] for span in trace["spans"]}
            for span in trace["spans"]:
                key = (span["traceID"].lower(), span["spanID"].lower())
                if key in identities:
                    raise RuntimeError("Duplicate archived trace/span ID")
                identities.add(key)
                service_by_span[key] = trace["processes"][span["processID"]][
                    "serviceName"]
                for ref in span.get("references") or []:
                    if ref.get("refType") == "CHILD_OF" and ref.get(
                            "spanID", "").lower() in local_services:
                        edges.add(local_services[ref["spanID"].lower()] +
                                  " -> " + service_by_span[key])
    return identities, service_by_span, edges


def storage(phase, first, source):
    window = first["window"]
    start_us = int(window["start_unix_ns"] // 1000)
    end_us = int(window["end_unix_ns"] // 1000)
    if source["query_start_utc"] != window["start_utc"] or \
            source["query_end_utc"] != window["end_utc"]:
        raise RuntimeError("Supplement UTC window differs from first capture")
    indices = list(source["indices"])
    expected_index = "jaeger-jaeger-span-" + window["start_utc"][:10]
    if indices != [expected_index] or source.get("trace_source") != \
            "JAEGER_OPENSEARCH_STORAGE":
        raise RuntimeError("Unexpected trace index or source")
    index = indices[0]
    expected_uuid = source["indices"][index]["uuid"]
    settings = request("/" + index + "/_settings?filter_path=*.settings.index.uuid")
    if settings[index]["settings"]["index"]["uuid"] != expected_uuid:
        raise RuntimeError("OpenSearch index UUID changed")
    query = {"size": 2000, "track_total_hits": True,
        "sort": ["_doc"], "_source": ["traceID", "spanID", "startTime",
            "startTimeMillis", "process", "operationName", "duration",
            "references", "tags"],
        "query": {"bool": {"filter": [
            {"range": {"startTimeMillis": {"gte": start_us // 1000,
                "lt": (end_us + 999) // 1000}}},
            {"range": {"startTime": {"gte": start_us, "lt": end_us}}}]}}}
    result = request("/" + index + "/_search?scroll=3m", query)
    expected_count = result["hits"]["total"]["value"]
    scroll_id = result.get("_scroll_id")
    seen, records = set(), {}
    pages = 0
    try:
        while True:
            if result.get("_shards", {}).get("failed", 0):
                raise RuntimeError("OpenSearch failed shards")
            hits = result["hits"]["hits"]
            if not hits:
                break
            pages += 1
            if pages > 100 or len(seen) + len(hits) > 200000:
                raise RuntimeError("Read budget exceeded")
            for hit in hits:
                row = hit["_source"]
                key = (row["traceID"].lower(), row["spanID"].lower())
                if key in seen:
                    raise RuntimeError("Duplicate storage trace/span ID")
                if not start_us <= row["startTime"] < end_us:
                    raise RuntimeError("Out-of-window span")
                seen.add(key)
                records[key] = row
            if len(seen) == expected_count:
                break
            result = request("/_search/scroll", {"scroll": "3m",
                "scroll_id": scroll_id})
            scroll_id = result.get("_scroll_id", scroll_id)
    finally:
        if scroll_id:
            request("/_search/scroll", {"scroll_id": [scroll_id]}, "DELETE")
    if len(seen) != expected_count:
        raise RuntimeError("Scroll count mismatch")
    return seen, records, pages, expected_uuid


def trace_classification(archive_root, manifest):
    files = {"ordinary_business": archive_root /
        "load_pair/state-machine/sessions.jsonl",
        "fixed_control_image": archive_root / "load_pair/controls/image_asset.jsonl",
        "fixed_control_cart": archive_root / "load_pair/controls/cart_view.jsonl"}
    result = {name: set() for name in files}
    for name, path in files.items():
        relative = str(path.relative_to(archive_root))
        if sha(path) != manifest["files"][relative]["sha256"]:
            raise RuntimeError("Business trace-ID source hash mismatch")
        for line in path.read_text().splitlines():
            if not line:
                continue
            row = json.loads(line)
            steps = row["steps"] if name == "ordinary_business" else [row]
            for step in steps:
                if step.get("trace_id"):
                    result[name].add(step["trace_id"].lower())
    return result


def evaluate(phase, archive_root, manifest, classes):
    base = archive_root / "capture-pipeline"
    first = json.loads((base / "captures" / phase / "capture-manifest.json").read_text())
    source = json.loads((base / "trace-completion" / phase /
                         "trace-storage-source.json").read_text())
    archived_ids, archived_services, archived_edges = archived(
        archive_root, manifest, phase)
    archived_trace_ids = {trace_id for trace_id, _ in archived_ids}
    known_trace_matches = {name: len(ids & archived_trace_ids)
                           for name, ids in classes.items()}
    if known_trace_matches["ordinary_business"] < 100 or any(
            known_trace_matches[name] < 30 for name in
            ("fixed_control_image", "fixed_control_cart")):
        raise RuntimeError("Recorded business/control Trace IDs do not align with archive")
    current_ids, current_records, pages, uuid = storage(phase, first, source)
    if not archived_ids <= current_ids:
        raise RuntimeError("Historical +60 IDs missing from current index")
    new_ids = sorted(current_ids - archived_ids)
    services = dict(archived_services)
    services.update({key: row["process"]["serviceName"] for key, row in
                     current_records.items()})
    pod_path = base / "captures" / phase / "kubernetes/pods.json"
    lag_path = base / "ingest-lag" / (phase + ".json")
    for path in (pod_path, lag_path):
        relative = str(path.relative_to(archive_root))
        if sha(path) != manifest["files"][relative]["sha256"]:
            raise RuntimeError("Archived Pod or lag snapshot hash mismatch")
    pods = {row["metadata"]["uid"]: row for row in json.loads(
        pod_path.read_text())["items"]}
    lag = json.loads(lag_path.read_text())
    plus_120 = next(row["trace_span_count"] for row in lag["samples"] if
                    row["planned_offset_seconds"] == 120)
    late = []
    for key in new_ids:
        row = current_records[key]
        resource_tags = {tag.get("key"): tag.get("value") for tag in
                         (row["process"].get("tags") or [])}
        parent_ids = [(ref.get("traceID", "").lower(),
                       ref.get("spanID", "").lower()) for ref in
                      (row.get("references") or []) if ref.get("refType") == "CHILD_OF"]
        service = row["process"]["serviceName"]
        parent_service = services.get(parent_ids[0]) if parent_ids else None
        uid = resource_tags.get("k8s.pod.uid")
        pod = pods.get(uid) if uid else None
        uid_status = ("UNATTRIBUTED_NO_UID" if uid is None else
            "UID_SERVICE_MATCH" if pod and pod["metadata"]["labels"].get(
                "opentelemetry.io/name") == service else
            "UID_NOT_IN_SAME_PHASE_SNAPSHOT_OR_SERVICE_MISMATCH")
        labels = [name for name, ids in classes.items() if key[0] in ids]
        late.append({"trace_id": key[0], "span_id": key[1],
            "service": service, "pod_uid": uid,
            "pod_name": resource_tags.get("k8s.pod.name"),
            "pod_uid_status": uid_status,
            "trace_classification": labels or ["INTERNAL_OR_UNVERIFIED"],
            "operation": row.get("operationName"),
            "start_us": row["startTime"], "duration_us": row.get("duration"),
            "parent_span_id": parent_ids[0][1] if parent_ids else None,
            "edge": parent_service + " -> " + service if parent_service else None,
            "exact_storage_write_time": None})
    late_edges = {row["edge"] for row in late if row["edge"]}
    critical = [row for row in late if any(name in row[
        "trace_classification"] for name in ("ordinary_business",
        "fixed_control_image", "fixed_control_cart"))]
    return {"phase": phase, "archived_plus_60_spans": len(archived_ids),
            "current_storage_spans": len(current_ids), "late_ids": late,
            "plus_120_recorded_span_count": plus_120,
            "current_minus_plus_120": len(current_ids) - plus_120,
            "critical_business_or_control_late_spans": len(critical),
            "critical_late_ids": [row["trace_id"] + "/" + row["span_id"]
                                  for row in critical],
            "known_trace_id_positive_controls_in_archive": known_trace_matches,
            "new_trace_ids": len({row["trace_id"] for row in late if
                row["trace_id"] not in {key[0] for key in archived_ids}}),
            "late_call_edges": sorted(late_edges),
            "new_call_edges_not_in_plus_60": sorted(late_edges - archived_edges),
            "pages": pages, "index_uuid": uuid,
            "write_time_resolution": "after +60 supplement and by +120 count check only"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True, choices=(
        "S5V3AP-VAL-1", "S5V3AP-VAL-2"))
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output exists")
    manifest = json.loads(args.source_manifest.read_text())
    classes = trace_classification(args.archive_root, manifest)
    phases = [evaluate(phase, args.archive_root, manifest, classes)
              for phase in ("before", "during", "after")]
    value = {"status": "READ_ONLY_LATE_ID_AUDIT", "at_utc":
        datetime.now(timezone.utc).isoformat(), "run_id": args.run_id,
        "source_manifest_sha256":
        sha(args.source_manifest), "phases": phases,
        "historic_archive_modified": False}
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": value["status"],
                      "late_counts": {row["phase"]: len(row["late_ids"])
                                      for row in phases}}))


if __name__ == "__main__":
    main()
