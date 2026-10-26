"""Classify archived parent references and public Pod identity without GT."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random

import yaml


PHASES = ("before", "during", "after")
VERSIONS = (("first", "captures"), ("plus60", "trace-completion"),
            ("plus120", "trace-final"), ("audit240", "trace-late-audit"))
IMPORTANT_EDGES = {"checkout->cart", "checkout->payment",
                   "frontend->recommendation", "frontend->product-catalog",
                   "frontend-proxy->image-provider", "frontend->cart"}


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(path.read_text())


def checked_lines(root, manifest, relative):
    path = root / relative
    if sha(path) != manifest["files"][str(relative)]["sha256"]:
        raise ValueError("Archived request source changed: " + str(relative))
    with path.open() as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def choose(edges, rng):
    draw = rng.random()
    cumulative = 0.0
    for edge in edges:
        cumulative += edge["probability"]
        if draw < cumulative:
            return edge["to"]
    return edges[-1]["to"]


def issued_context(root, manifest, machine, plan, people_count, seed):
    issued = {}
    specs = {row["name"]: row for row in plan["workload"]["control_streams"]}
    for name in ("image_asset", "cart_view"):
        offset = specs[name]["seed_offset"]
        for row in checked_lines(root, manifest, Path("load_pair/controls") /
                                 (name + ".jsonl")):
            if not row.get("trace_id"):
                continue
            rng = random.Random(((seed + offset) << 32) + row["index"])
            trace_id = f"{rng.getrandbits(128):032x}"
            parent_id = f"{rng.getrandbits(64):016x}"
            if trace_id != row["trace_id"] or trace_id in issued:
                raise ValueError("Control external parent reconstruction failed")
            issued[trace_id] = {"origin": name, "external_parent_id": parent_id}
    for session in checked_lines(root, manifest,
                                 Path("load_pair/state-machine/sessions.jsonl")):
        rng = random.Random((seed << 32) + session["index"])
        rng.choice(machine["products"])
        rng.choice(machine["categories"])
        rng.choice([1, 2, 3, 4, 5, 10])
        rng.randrange(people_count)
        flow = machine["workflows"][session["workflow"]]
        node = flow["start"]
        for step in session["steps"]:
            trace_id = f"{rng.getrandbits(128):032x}"
            parent_id = f"{rng.getrandbits(64):016x}"
            if (trace_id != step.get("trace_id") or node != step["operation"] or
                    trace_id in issued):
                raise ValueError("State-machine external parent reconstruction failed")
            issued[trace_id] = {"origin": "state_machine/" + node,
                                "external_parent_id": parent_id}
            if not step["success"]:
                break
            node = choose(flow["transitions"][node], rng)
    return issued


def iter_version(root, manifest, version):
    folder = dict(VERSIONS)[version]
    for phase in PHASES:
        base = root / "capture-pipeline" / folder / phase / "trace-storage-pages"
        for path in sorted(base.glob("page-*.json")):
            relative = path.relative_to(root)
            contents = path.read_bytes()
            if hashlib.sha256(contents).hexdigest() != manifest["files"][str(
                    relative)]["sha256"]:
                raise ValueError("Archived trace page changed: " + str(relative))
            for hit in json.loads(contents)["hits"]["hits"]:
                yield phase, hit["_source"]


def pod_maps(root):
    result = {}
    for phase in PHASES:
        pods = read(root / "capture-pipeline/captures" / phase /
                    "kubernetes/pods.json")["items"]
        by_uid = {}
        by_container = defaultdict(list)
        by_service = defaultdict(list)
        for pod in pods:
            info = {"uid": pod["metadata"]["uid"],
                "name": pod["metadata"]["name"],
                "service": pod["metadata"]["labels"].get("opentelemetry.io/name"),
                "node": pod["spec"].get("nodeName"),
                "pod_ip": pod["status"].get("podIP")}
            by_uid[info["uid"]] = info
            by_service[info["service"]].append(info)
            for container in pod["status"].get("containerStatuses") or []:
                identity = str(container.get("containerID") or "").split("://")[-1]
                if identity:
                    by_container[identity].append(info)
        result[phase] = (by_uid, by_container, by_service)
    return result


def identity(span, phase, maps):
    by_uid, by_container, by_service = maps[phase]
    resource = span["resource"]
    uid = resource.get("k8s.pod.uid")
    service = span["service"]
    if uid:
        pod = by_uid.get(uid)
        if pod and pod["service"] == service:
            return "UID_PRESENT_AND_VERIFIED", "resource_uid", uid
        return "UID_MISMATCH", "resource_uid", uid
    container = resource.get("container.id")
    matches = [pod for pod in by_container.get(container, [])
               if pod["service"] == service]
    if len(matches) == 1:
        return "UID_UNIQUELY_INFERABLE_FROM_PUBLIC_TOPOLOGY", \
               "container_id", matches[0]["uid"]
    candidates = by_service.get(service, [])
    if len(candidates) == 1:
        return "UID_UNIQUELY_INFERABLE_FROM_PUBLIC_TOPOLOGY", \
               "unique_service_pod", candidates[0]["uid"]
    return "UID_AMBIGUOUS", "no_unique_public_mapping", None


def compact(source, phase):
    process = source.get("process") or {}
    resource = {item.get("key"): item.get("value") for item in
                (process.get("tags") or [])}
    tags = {item.get("key"): item.get("value") for item in
            (source.get("tags") or [])}
    return {"phase": phase, "trace_id": source["traceID"],
        "span_id": source["spanID"], "service": process.get("serviceName"),
        "operation": source.get("operationName"),
        "kind": (source.get("tag") or {}).get("span@kind"),
        "start_us": source.get("startTime"),
        "duration_us": source.get("duration"),
        "references": [(ref.get("refType"), ref.get("traceID"), ref.get("spanID"))
                       for ref in source.get("references") or []],
        "resource": resource, "flags": source.get("flags"),
        "link_tag_count": sum("link" in str(key).lower() for key in tags),
        "status": tags.get("otel.status_code") or tags.get("http.status_code")}


def missing_classification(child, ref, span_index, audit_keys, issued):
    _, parent_trace, parent_span = ref
    parent_key = (parent_trace, parent_span)
    if parent_key in span_index:
        parent = span_index[parent_key]
        return ("CROSS_PHASE_PARENT_FOUND" if parent["phase"] != child["phase"]
                else "SAME_PHASE_PARENT_FOUND")
    if parent_key in audit_keys:
        return "PARENT_LATE_AFTER_PLUS120"
    origin = issued.get(child["trace_id"])
    if (origin and parent_trace == child["trace_id"] and
            parent_span == origin["external_parent_id"]):
        return "VERIFIED_UNEXPORTED_EXTERNAL_PARENT"
    return "UNKNOWN_UNRESOLVED_PARENT"


def audit(root, manifest, machine, plan, people_count):
    schedule = read(root / "load_pair/schedule.json")
    issued = issued_context(root, manifest, machine, plan, people_count,
                            schedule["seed"])
    maps = pod_maps(root)
    span_index = {}
    service_counts = defaultdict(Counter)
    uid_status = defaultdict(Counter)
    uid_methods = defaultdict(Counter)
    uid_examples = defaultdict(list)
    flag_counts = Counter()
    link_tag_count = 0
    for phase, source in iter_version(root, manifest, "plus120"):
        row = compact(source, phase)
        key = (row["trace_id"], row["span_id"])
        if key in span_index:
            raise ValueError("Duplicate trace/span identity across final phase windows")
        span_index[key] = row
        service_counts[row["service"]]["total"] += 1
        service_counts[row["service"]]["kind_" + str(row["kind"])] += 1
        flag_counts[str(row["flags"])] += 1
        link_tag_count += row["link_tag_count"]
        if row["kind"] == "server":
            category, method, uid = identity(row, phase, maps)
            uid_status[row["service"]][category] += 1
            if not row["resource"].get("k8s.pod.uid"):
                uid_status[row["service"]]["UID_MISSING_RAW"] += 1
            uid_methods[row["service"]][method] += 1
            if len(uid_examples[row["service"]]) < 3:
                uid_examples[row["service"]].append({"trace_id": row["trace_id"],
                    "span_id": row["span_id"], "resource_uid": row[
                        "resource"].get("k8s.pod.uid"), "container_id": row[
                        "resource"].get("container.id"), "associated_uid": uid,
                    "category": category, "method": method, "phase": phase})
    first_keys = {(row["traceID"], row["spanID"]) for _, row in
                  iter_version(root, manifest, "first")}
    plus60_keys = {(row["traceID"], row["spanID"]) for _, row in
                   iter_version(root, manifest, "plus60")}
    audit_keys = {(row["traceID"], row["spanID"]) for _, row in
                  iter_version(root, manifest, "audit240")}
    if not first_keys <= plus60_keys <= span_index.keys() <= audit_keys:
        raise ValueError("Immutable Trace identity chain is not monotone")

    categories = Counter()
    old_issued_server_categories = Counter()
    by_service = defaultdict(Counter)
    edges = defaultdict(Counter)
    unresolved_samples = []
    all_ref_count = 0
    old_issued_server_missing = 0
    cross_phase = 0
    supplemented_parent_count = Counter()
    trace_unknown = set()
    for key, child in span_index.items():
        for ref in child["references"]:
            if ref[0] != "CHILD_OF" or not ref[1] or not ref[2]:
                categories["NON_CHILD_OF_OR_INCOMPLETE_REFERENCE"] += 1
                continue
            all_ref_count += 1
            category = missing_classification(child, ref, span_index, audit_keys,
                                              issued)
            categories[category] += 1
            by_service[child["service"]][category] += 1
            is_old_universe = child["kind"] == "server" and \
                child["trace_id"] in issued
            if is_old_universe:
                old_issued_server_categories[category] += 1
                if category not in ("SAME_PHASE_PARENT_FOUND",
                                    "CROSS_PHASE_PARENT_FOUND"):
                    old_issued_server_missing += 1
            if category == "CROSS_PHASE_PARENT_FOUND":
                cross_phase += 1
            if category == "UNKNOWN_UNRESOLVED_PARENT":
                if child["trace_id"] in issued:
                    trace_unknown.add(child["trace_id"])
                if len(unresolved_samples) < 30:
                    unresolved_samples.append({"child_trace_id": child["trace_id"],
                        "child_span_id": child["span_id"],
                        "parent_trace_id": ref[1], "parent_span_id": ref[2],
                        "service": child["service"], "operation": child["operation"],
                        "kind": child["kind"], "phase": child["phase"],
                        "flags": child["flags"]})
            parent = span_index.get((ref[1], ref[2]))
            if parent and child["kind"] == "server" and parent["kind"] == "client":
                edge = str(parent["service"]) + "->" + str(child["service"])
                edges[edge]["matched_client_server"] += 1
                if edge in IMPORTANT_EDGES:
                    edges[edge]["important_edge"] += 1
            if parent and (ref[1], ref[2]) not in first_keys:
                supplemented_parent_count["absent_first_present_final"] += 1
            if parent and (ref[1], ref[2]) not in plus60_keys:
                supplemented_parent_count["absent_plus60_present_final"] += 1

    version_missing = {}
    for name, keys in (("first", first_keys), ("plus60", plus60_keys),
                       ("plus120", set(span_index)), ("audit240", audit_keys)):
        missing = 0
        for key in keys:
            child = span_index.get(key)
            if not child or child["kind"] != "server" or child[
                    "trace_id"] not in issued:
                continue
            missing += sum(ref[0] == "CHILD_OF" and (ref[1], ref[2]) not in keys
                           for ref in child["references"])
        version_missing[name] = missing
    if version_missing["plus120"] != old_issued_server_missing:
        raise ValueError("Old missing-parent denominator could not be reproduced")

    fixed_edges = {}
    for origin, desired in (("image_asset", "frontend-proxy->image-provider"),
                            ("cart_view", "frontend->cart")):
        ids = {trace for trace, meta in issued.items() if meta["origin"] == origin}
        matching = set()
        for child in span_index.values():
            if child["trace_id"] not in ids or child["kind"] != "server":
                continue
            for ref in child["references"]:
                parent = span_index.get((ref[1], ref[2]))
                if parent and parent["kind"] == "client" and (
                        str(parent["service"]) + "->" + str(child["service"])) == desired:
                    matching.add(child["trace_id"])
        fixed_edges[desired] = {"issued_trace_ids": len(ids),
            "client_server_matched_trace_ids": len(matching),
            "coverage": len(matching) / len(ids) if ids else None}

    return {"run_id": schedule["run_id"], "seed": schedule["seed"],
        "issued_trace_ids": len(issued), "final_span_ids": len(span_index),
        "first_span_ids": len(first_keys), "plus60_span_ids": len(plus60_keys),
        "audit240_span_ids": len(audit_keys),
        "all_child_of_reference_count": all_ref_count,
        "all_reference_categories": dict(categories),
        "old_issued_server_missing_parent_count": old_issued_server_missing,
        "old_issued_server_reference_categories": dict(old_issued_server_categories),
        "old_missing_by_immutable_version": version_missing,
        "cross_phase_parent_count_all_spans": cross_phase,
        "supplemented_parent_references": dict(supplemented_parent_count),
        "service_span_and_kind_counts": {name: dict(value) for name, value in
                                         service_counts.items()},
        "missing_parent_reference_categories_by_child_service": {
            name: dict(value) for name, value in by_service.items()},
        "client_server_edges": {name: dict(value) for name, value in edges.items()},
        "fixed_control_edge_trace_coverage": fixed_edges,
        "pod_uid_server_status_by_service": {name: dict(value) for name, value in
                                             uid_status.items()},
        "pod_uid_inference_methods_by_service": {name: dict(value) for name, value
                                                  in uid_methods.items()},
        "pod_uid_examples_by_service": dict(uid_examples),
        "unknown_issued_trace_ids": len(trace_unknown),
        "unresolved_parent_examples": unresolved_samples,
        "flags_distribution": dict(flag_counts),
        "span_link_tag_count": link_tag_count}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-base", type=Path, required=True)
    parser.add_argument("--manifest-root", type=Path, required=True)
    parser.add_argument("--batch", choices=("cgv2-val1", "cgv2-val2"), required=True)
    parser.add_argument("--machine", type=Path, required=True)
    parser.add_argument("--load-plan", type=Path, required=True)
    parser.add_argument("--people-count", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    root = args.archive_base / args.batch
    manifest_path = args.manifest_root / (args.batch + "-source-manifest.json")
    manifest = read(manifest_path)
    if manifest["batch"] != args.batch or args.people_count < 1:
        raise ValueError("Invalid archived source or synthetic people count")
    machine = yaml.safe_load(args.machine.read_text())
    plan = yaml.safe_load(args.load_plan.read_text())
    value = audit(root, manifest, machine, plan, args.people_count)
    value.update({"status": "READ_ONLY_TRACE_PARENT_AND_UID_AUDIT",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_manifest_sha256": sha(manifest_path),
        "analysis_code_sha256": sha(Path(__file__)),
        "machine_sha256": sha(args.machine),
        "load_plan_sha256": sha(args.load_plan),
        "private_gt_read": False})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": value["status"], "run_id": value["run_id"],
        "old_missing": value["old_issued_server_missing_parent_count"],
        "categories": value["old_issued_server_reference_categories"]}))


if __name__ == "__main__":
    main()
