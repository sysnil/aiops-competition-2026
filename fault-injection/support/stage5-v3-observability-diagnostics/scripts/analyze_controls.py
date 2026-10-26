"""Recompute control latency and join public proxy/span/resource evidence."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import shlex

import yaml


RUNS = (("cgv2-val1", "S5V3AP-CGV2-VAL-1"),
        ("cgv2-val2", "S5V3AP-CGV2-VAL-2"))
CONTROLS = ("image_asset", "cart_view")
PHASES = ("before", "during", "after")
METRICS = ("node_cpu", "node_memory", "cpu", "memory", "cfs_periods",
           "cfs_throttled_periods", "cfs_throttled_seconds")


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def checked(root, manifest, relative):
    path = root / relative
    if sha(path) != manifest["files"][str(relative)]["sha256"]:
        raise ValueError("Archived input hash mismatch: " + str(relative))
    return path


def read(path):
    return json.loads(path.read_text())


def rows(path):
    with path.open() as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)] if ordered else None


def to_utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def minute(seconds, start):
    return int((seconds - start) // 60)


def control_parent(seed, offset, index):
    rng = random.Random(((seed + offset) << 32) + index)
    return f"{rng.getrandbits(128):032x}", f"{rng.getrandbits(64):016x}"


def proxy_access(source):
    attributes = source.get("attributes") or {}
    if attributes.get("event.name") != "proxy.access":
        return None
    try:
        fields = shlex.split(source["body"])
        duration = float(fields[9]) if fields[9] != "-" else None
        upstream_ms = float(fields[10]) if fields[10] != "-" else None
        return {"http_status": int(fields[2]), "response_flags": fields[3],
                "response_code_details": fields[4], "proxy_duration_ms": duration,
                "upstream_service_time_ms": upstream_ms,
                "upstream_host": attributes.get("upstream.host"),
                "upstream_cluster": attributes.get("upstream.cluster"),
                "url_path": attributes.get("url.path"),
                "start_utc": fields[0].strip("[]"),
                "log_timestamp_utc": source.get("@timestamp"),
                "raw_body_sha256": hashlib.sha256(source["body"].encode()).hexdigest()}
    except (IndexError, ValueError, KeyError):
        return {"parse_status": "UNPARSEABLE", "url_path": attributes.get("url.path"),
                "raw_body_sha256": hashlib.sha256(source.get("body", "").encode()).hexdigest()}


def iter_hits(directory):
    for path in sorted(directory.glob("page-*.json")):
        page = read(path)
        for index, hit in enumerate(page["hits"]["hits"]):
            yield path, index, hit["_source"]


def compact_span(source, phase, page, index, pod_by_uid):
    process = source.get("process") or {}
    resource = {item.get("key"): item.get("value") for item in
                process.get("tags") or []}
    uid = resource.get("k8s.pod.uid")
    pod = pod_by_uid.get(uid) if uid else None
    container_id = resource.get("container.id")
    inferred = [item for item in pod_by_uid.values() if container_id and any(
        str(value).split("://")[-1] == container_id for value in
        item["container_ids"])]
    inferred = inferred[0] if len(inferred) == 1 and inferred[0]["service"] == \
        process.get("serviceName") else None
    return {"trace_id": source["traceID"], "span_id": source["spanID"],
        "phase": phase, "service": process.get("serviceName"),
        "operation": source.get("operationName"),
        "kind": (source.get("tag") or {}).get("span@kind"),
        "start_us": source.get("startTime"), "duration_us": source.get("duration"),
        "parent_ids": [ref.get("spanID") for ref in source.get("references") or []
                       if ref.get("refType") == "CHILD_OF"],
        "pod_uid": uid, "pod_uid_match": bool(pod and pod["service"] ==
            process.get("serviceName")),
        "pod_name": pod["name"] if pod else None,
        "node": pod["node"] if pod else None,
        "inferred_pod_uid_from_container_id": inferred["uid"] if inferred else None,
        "inferred_pod_name_from_container_id": inferred["name"] if inferred else None,
        "inferred_node_from_container_id": inferred["node"] if inferred else None,
        "container_id": container_id,
        "source_page": str(page), "source_hit_index": index}


def pods(root):
    result = {}
    for phase in PHASES:
        source = read(root / "capture-pipeline/captures" / phase /
                      "kubernetes/pods.json")
        result[phase] = {item["metadata"]["uid"]: {
            "uid": item["metadata"]["uid"],
            "name": item["metadata"]["name"],
            "service": item["metadata"]["labels"].get("opentelemetry.io/name"),
            "node": item["spec"].get("nodeName"),
            "pod_ip": item["status"].get("podIP"),
            "container_ids": [row.get("containerID") for row in
                              item["status"].get("containerStatuses") or []]}
            for item in source["items"]}
    return result


def summarize_requests(items, start, seed, offsets, rates):
    by_control = defaultdict(list)
    for control, source_rows in items.items():
        rate = rates[control]
        for row in source_rows:
            done = datetime.fromisoformat(row.get("time_utc") or row[
                "scheduled_utc"]).timestamp()
            latency = row.get("latency_ms")
            begun = done - latency / 1000 if isinstance(latency, (float, int)) else None
            planned = start + row["index"] / rate
            generated_trace, parent = control_parent(seed, offsets[control],
                                                      row["index"])
            if row.get("trace_id") and row["trace_id"] != generated_trace:
                raise ValueError("Control Trace ID differs from locked generator")
            by_control[control].append({"index": row["index"],
                "trace_id": row.get("trace_id"),
                "external_parent_span_id": parent if row.get("trace_id") else None,
                "status_code": row.get("status_code"),
                "error_type": row.get("error_type"), "success": row.get("success"),
                "latency_ms": latency,
                "completion_utc": to_utc(done),
                "inferred_start_utc": to_utc(begun) if begun is not None else None,
                "planned_utc": to_utc(planned),
                "completion_minute": minute(done, start),
                "start_minute": minute(begun, start) if begun is not None else None,
                "start_lag_ms": round((begun - planned) * 1000, 3)
                    if begun is not None else None})
    return by_control


def stats(by_control, start):
    result = {}
    for control, requests in by_control.items():
        bins = [[] for _ in range(25)]
        by_start = [[] for _ in range(25)]
        for row in requests:
            if 0 <= row["completion_minute"] < 25:
                bins[row["completion_minute"]].append(row)
            if row["start_minute"] is not None and 0 <= row["start_minute"] < 25:
                by_start[row["start_minute"]].append(row)
        before_p95 = [percentile([item["latency_ms"] for item in bucket
                    if item["success"]], .95) for bucket in bins[:10]]
        baseline = percentile(before_p95, .5)
        minutes = []
        for index, bucket in enumerate(bins):
            successful = [item["latency_ms"] for item in bucket
                          if item["success"] and item["latency_ms"] is not None]
            percentiles = {"p" + str(int(f * 100)): percentile(successful, f)
                           for f in (.50, .90, .95, .99)}
            p95 = percentiles["p95"]
            minutes.append({"minute": index,
                "window_start_utc": to_utc(start + index * 60),
                "attempted": len(bucket),
                "successful": sum(item["success"] for item in bucket),
                "failed": sum(not item["success"] for item in bucket),
                "start_bucket_count": len(by_start[index]),
                "start_vs_completion_index_difference": len({item["index"]
                    for item in bucket} ^ {item["index"] for item in by_start[index]}),
                **percentiles,
                "p95_absolute_change_ms": p95 - baseline if p95 is not None else None,
                "p95_ratio": p95 / baseline if p95 is not None else None,
                "max_ms": max(successful) if successful else None,
                "above_before_p95_count": sum(value > baseline for value in successful),
                "above_1_25_baseline_count": sum(value > 1.25 * baseline
                                                   for value in successful)})
        evaluated = [item["latency_ms"] for item in requests if item["success"]
                     and 10 <= item["completion_minute"] < 25]
        result[control] = {"baseline_median_minute_p95_ms": baseline,
            "minutes": minutes,
            "pooled_evaluated_p95_ms": percentile(evaluated, .95),
            "pooled_evaluated_p95_ratio": percentile(evaluated, .95) / baseline,
            "minute_p95_max_ratio": max(item["p95_ratio"] for item in minutes[10:]),
            "client_status_counts": dict(Counter(str(item["status_code"])
                if item["status_code"] is not None else item["error_type"]
                for item in requests)),
            "boundary_crossing_requests": sum(item["start_minute"] !=
                item["completion_minute"] for item in requests),
            "top_slow_requests": sorted(requests, key=lambda row:
                row["latency_ms"] if row["latency_ms"] is not None else -1,
                reverse=True)[:20]}
    return result


def metric_context(root, start):
    output = {}
    for phase in PHASES:
        base = root / "capture-pipeline/captures" / phase / "metrics"
        for family in METRICS:
            path = base / (family + ".json")
            if not path.is_file():
                continue
            series = read(path)["data"]["result"]
            for row in series:
                labels = row["metric"]
                node = labels.get("k8s_node_name") or labels.get("instance")
                pod = labels.get("pod", "")
                relevant_node = family.startswith("node_") and node in (
                    "k8s-worker1", "k8s-worker2", "k8s-worker3")
                relevant_pod = not family.startswith("node_") and pod.startswith((
                    "cart-", "frontend-", "frontend-proxy-", "image-provider-",
                    "otel-collector-"))
                if not (relevant_node or relevant_pod):
                    continue
                key = family + "/" + (pod or node) + "/" + labels.get("container", "")
                values = []
                for stamp, value in row.get("values", []):
                    if (8 <= minute(float(stamp), start) <= 14 and
                            value not in ("NaN", "+Inf", "-Inf")):
                        values.append([float(stamp), float(value)])
                if values:
                    output.setdefault(key, []).extend(values)
    return {key: sorted(set(tuple(value) for value in values))
            for key, values in output.items()}


def one_run(root, manifest, machine):
    schedule = read(root / "load_pair/schedule.json")
    start = datetime.fromisoformat(schedule["scheduled_start_utc"]).timestamp()
    specs = {row["name"]: row for row in machine["workload"]["control_streams"]}
    offsets = {name: int(specs[name]["seed_offset"]) for name in CONTROLS}
    rates = {name: float(specs[name]["rate_per_second"]) for name in CONTROLS}
    request_sources = {}
    for control in CONTROLS:
        relative = Path("load_pair/controls") / (control + ".jsonl")
        request_sources[control] = list(rows(checked(root, manifest, relative)))
    requests = summarize_requests(request_sources, start, schedule["seed"], offsets,
                                  rates)
    summary = stats(requests, start)
    trace_ids = {item["trace_id"] for rows_ in requests.values() for item in rows_
                 if item["trace_id"]}
    proxy = {}
    spans = defaultdict(list)
    pod_maps = pods(root)
    for phase in PHASES:
        for page, index, source in iter_hits(root / "capture-pipeline/captures" /
                                            phase / "logs"):
            if source.get("traceId") in trace_ids:
                parsed = proxy_access(source)
                if parsed is not None:
                    parsed.update({"source_page": str(page), "source_hit_index": index})
                    proxy.setdefault(source["traceId"], []).append(parsed)
        for page, index, source in iter_hits(root / "capture-pipeline/trace-final" /
                                            phase / "trace-storage-pages"):
            if source["traceID"] in trace_ids:
                spans[source["traceID"]].append(compact_span(source, phase,
                                              page, index, pod_maps[phase]))
    selected = []
    for control, control_rows in requests.items():
        for item in control_rows:
            if not (8 <= item["completion_minute"] <= 14):
                continue
            trace = item["trace_id"]
            related = spans.get(trace, []) if trace else []
            by_service_kind = defaultdict(list)
            for span in related:
                by_service_kind[(span["service"], span["kind"])].append(span)
            target = "image-provider" if control == "image_asset" else "cart"
            servers = by_service_kind[(target, "server")]
            client = by_service_kind[("frontend-proxy", "client")] if control == \
                "image_asset" else by_service_kind[("frontend", "client")]
            frontend_server = by_service_kind[("frontend", "server")]
            baseline = summary[control]["baseline_median_minute_p95_ms"]
            selected.append({**item, "control": control,
                "proxy_access": proxy.get(trace, []),
                "target_server_spans": servers,
                "relevant_client_spans": client,
                "frontend_server_spans": frontend_server,
                "span_count": len(related),
                "target_server_span_count": len(servers),
                "target_server_max_duration_ms": max((span["duration_us"] / 1000
                    for span in servers), default=None),
                "client_span_max_duration_ms": max((span["duration_us"] / 1000
                    for span in client), default=None),
                "frontend_server_max_duration_ms": max((span["duration_us"] / 1000
                    for span in frontend_server), default=None),
                "slow_span_outline": [{"service": span["service"],
                    "kind": span["kind"], "operation": span["operation"],
                    "duration_ms": span["duration_us"] / 1000,
                    "start_us": span["start_us"], "pod_uid": span["pod_uid"],
                    "inferred_pod_uid_from_container_id": span[
                        "inferred_pod_uid_from_container_id"]}
                    for span in sorted(related, key=lambda value: value["start_us"])
                    ] if item["latency_ms"] is not None and
                    item["latency_ms"] > 1.25 * baseline else []})
    return {"run_id": schedule["run_id"], "seed": schedule["seed"],
        "source_root": str(root), "start_utc": schedule["scheduled_start_utc"],
        "control_summary": summary, "selected_request_count": len(selected),
        "selected_requests": selected,
        "all_control_trace_ids": len(trace_ids),
        "proxy_access_matched_trace_ids": len(proxy),
        "span_matched_trace_ids": len(spans),
        "resource_metric_context": metric_context(root, start)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-base", type=Path, required=True)
    parser.add_argument("--manifest-root", type=Path, required=True)
    parser.add_argument("--load-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    plan = yaml.safe_load(args.load_plan.read_text())
    result = {"status": "ARCHIVED_CONTROL_REQUEST_TRACE_AUDIT",
        "analysis_code_sha256": sha(Path(__file__)),
        "load_plan_sha256": sha(args.load_plan), "runs": []}
    for batch, run_id in RUNS:
        root = args.archive_base / batch
        manifest_path = args.manifest_root / (batch + "-source-manifest.json")
        manifest = read(manifest_path)
        if (manifest["batch"] != batch or
                manifest["source"].split("/")[-1] != run_id):
            raise ValueError("Archive manifest identifies another run")
        value = one_run(root, manifest, plan)
        value["source_manifest_sha256"] = sha(manifest_path)
        result["runs"].append(value)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "runs": [{
        "run_id": row["run_id"], "selected_requests": row["selected_request_count"],
        "proxy_matches": row["proxy_access_matched_trace_ids"],
        "span_matches": row["span_matched_trace_ids"]} for row in result["runs"]]}))


if __name__ == "__main__":
    main()
