"""Classify fixed +240 audit without changing the frozen +120 scoring archive."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from trace_identity import span_map, subset


FLAGD_OPERATION = "flagd.evaluation.v2.Service/EventStream"
FLAGD_SERVICES = {"ad", "payment", "recommendation", "fraud-detection", "flagd"}
PHASES = ("before", "during", "after")


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def issued(root):
    sources = {"ordinary_business": root / "load_pair/state-machine/sessions.jsonl",
               "fixed_control_image": root / "load_pair/controls/image_asset.jsonl",
               "fixed_control_cart": root / "load_pair/controls/cart_view.jsonl"}
    classes = {name: set() for name in sources}
    for name, path in sources.items():
        for line in path.read_text().splitlines():
            if line:
                row = json.loads(line)
                for step in row["steps"] if name == "ordinary_business" else [row]:
                    if step.get("trace_id"):
                        classes[name].add(step["trace_id"].lower())
    return classes, {str(path): sha(path) for path in sources.values()}


def classify_late(key, row, all_rows, classes, pod_by_uid):
    if any(key[0] in ids for ids in classes.values()):
        return "CRITICAL_BUSINESS_OR_CONTROL"
    same_trace = [item[1] for identity, item in all_rows.items()
                  if identity[0] == key[0]]
    if not same_trace or any(span.get("process", {}).get("serviceName") not in
            FLAGD_SERVICES or not str(span.get("operationName", "")).endswith(
                FLAGD_OPERATION) for span in same_trace):
        return "UNCLASSIFIED_LATE"
    tags = {tag.get("key"): tag.get("value") for tag in
            (row.get("process", {}).get("tags") or [])}
    uid = tags.get("k8s.pod.uid")
    if uid:
        pod = pod_by_uid.get(uid)
        if not pod or pod["metadata"]["labels"].get(
                "opentelemetry.io/name") != row["process"]["serviceName"]:
            return "UNCLASSIFIED_LATE"
    return "VERIFIED_NONCRITICAL_CONTROL_PLANE"


def evaluate(root, phase, classes):
    capture = root / "capture-pipeline"
    final_root = capture / "trace-final" / phase
    audit_root = capture / "trace-late-audit" / phase
    final_manifest = read(final_root / "final-manifest.json")
    audit_manifest = read(audit_root / "late-audit-manifest.json")
    if final_manifest["status"] != "FINAL_SNAPSHOT_FROZEN" or \
            audit_manifest["status"] != "LATE_AUDIT_FROZEN_UNCLASSIFIED" or \
            audit_manifest["final_manifest_sha256"] != sha(final_root /
                "final-manifest.json") or \
            audit_manifest["scoring_archive_changed_by_audit"]:
        raise ValueError("Immutable final/audit manifest chain broken")
    first = span_map(capture / "captures" / phase)
    middle = span_map(capture / "trace-completion" / phase)
    final = span_map(final_root)
    later = span_map(audit_root)
    subset(first, middle)
    subset(middle, final)
    subset(final, later)
    traces = {key[0] for key in final}
    positive = {name: len(ids & traces) for name, ids in classes.items()}
    if positive["ordinary_business"] < 100 or any(positive[name] < 30
            for name in ("fixed_control_image", "fixed_control_cart")):
        raise ValueError("Issued business/control Trace IDs do not match final archive")
    pods = {item["metadata"]["uid"]: item for item in read(
        capture / "captures" / phase / "kubernetes/pods.json")["items"]}
    late = []
    for key in sorted(later.keys() - final.keys()):
        row = later[key][1]
        late.append({"trace_id": key[0], "span_id": key[1],
            "service": row["process"]["serviceName"],
            "operation": row.get("operationName"),
            "classification": classify_late(key, row, later, classes, pods)})
    kinds = {row["classification"] for row in late}
    if "CRITICAL_BUSINESS_OR_CONTROL" in kinds:
        status = "CAPTURE_INCOMPLETE_CRITICAL_LATE"
    elif "UNCLASSIFIED_LATE" in kinds:
        status = "CAPTURE_PARTIAL_UNVERIFIED"
    elif late:
        status = "BOUNDED_COMPLETION_WITH_NONCRITICAL_LATE_AUDIT"
    elif len(first) == len(final):
        status = "LIVE_COMPLETE"
    else:
        status = "BOUNDED_DELAY_COMPLETE"
    return {"phase": phase, "status": status, "first_ids": len(first),
        "plus60_ids": len(middle), "final_scoring_ids": len(final),
        "audit_ids": len(later), "positive_control_matches": positive,
        "late_ids": late, "final_manifest_sha256": sha(final_root /
            "final-manifest.json"), "late_audit_manifest_sha256": sha(audit_root /
            "late-audit-manifest.json"),
        "scoring_snapshot_changed_by_audit": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    classes, source_hashes = issued(args.run)
    phases = [evaluate(args.run, phase, classes) for phase in PHASES]
    accepted = {"LIVE_COMPLETE", "BOUNDED_DELAY_COMPLETE",
                "BOUNDED_COMPLETION_WITH_NONCRITICAL_LATE_AUDIT"}
    value = {"status": "CANDIDATE_TRACE_QUALITY_PASS" if all(row[
        "status"] in accepted for row in phases) else
        "CANDIDATE_TRACE_QUALITY_FAIL", "at_utc":
        datetime.now(timezone.utc).isoformat(), "run_id": args.run.name,
        "phases": phases, "issued_request_source_sha256": source_hashes,
        "formal_stage5_v3_trace_contract_replaced": False}
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": value["status"], "phases": [{
        "phase": row["phase"], "status": row["status"],
        "late": len(row["late_ids"])} for row in phases]}))


if __name__ == "__main__":
    main()
