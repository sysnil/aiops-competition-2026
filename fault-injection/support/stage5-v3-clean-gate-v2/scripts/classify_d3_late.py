"""Classify already-audited D3 late IDs against archived issued requests."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ARCHIVE = Path("/data/rca-archives/stage5-v3-20261012/baseline-runs")
RUN = ARCHIVE / "S5V3AP-CAL-D3"
MANIFEST = Path("/root/stage5-v3-archive-verify/baseline-runs-source-manifest.json")
AUDIT = Path("/root/stage5-v3-archive-verify/d3-late-span-id-audit-v3.json")
OUTPUT = Path("/root/stage5-v3-clean-gate-v2/results/D3-late-classification.json")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked(path, manifest):
    relative = str(path.relative_to(ARCHIVE))
    if sha(path) != manifest["files"][relative]["sha256"]:
        raise RuntimeError("D3 source hash mismatch: " + relative)
    return path


def main():
    if OUTPUT.exists():
        raise ValueError("Refusing overwrite")
    manifest = json.loads(MANIFEST.read_text())
    audit = json.loads(AUDIT.read_text())
    sources = {
        "ordinary_business": RUN / "load_pair/state-machine/sessions.jsonl",
        "fixed_control_image": RUN / "load_pair/controls/image_asset.jsonl",
        "fixed_control_cart": RUN / "load_pair/controls/cart_view.jsonl"}
    issued = {name: set() for name in sources}
    for name, path in sources.items():
        for line in checked(path, manifest).read_text().splitlines():
            if line:
                row = json.loads(line)
                for step in row["steps"] if name == "ordinary_business" else [row]:
                    if step.get("trace_id"):
                        issued[name].add(step["trace_id"].lower())
    phases = []
    for phase in audit["phases"]:
        name = phase["phase"]
        traced = set()
        for path in sorted((RUN / "capture-pipeline/trace-completion" / name /
                            "traces").glob("storage-*.json")):
            for trace in json.loads(checked(path, manifest).read_text())["data"]:
                traced.add(trace["traceID"].lower())
        positive = {kind: len(ids & traced) for kind, ids in issued.items()}
        if positive["ordinary_business"] < 100 or any(positive[key] < 30
            for key in ("fixed_control_image", "fixed_control_cart")):
            raise RuntimeError("D3 request-ID positive control missing")
        late = [{"trace_id": row["trace_id"], "span_id": row["span_id"],
                 "service": row["service"], "classification": [kind for kind,
                 ids in issued.items() if row["trace_id"] in ids] or
                 ["INTERNAL_OR_UNVERIFIED"]} for row in phase["late_ids"]]
        phases.append({"phase": name, "positive_control_matches": positive,
            "late": late, "critical_business_or_control_late_spans": sum(
                row["classification"] != ["INTERNAL_OR_UNVERIFIED"]
                for row in late)})
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    value = {"status": "D3_LATE_REQUEST_CLASSIFICATION_COMPLETE",
        "at_utc": datetime.now(timezone.utc).isoformat(), "phases": phases,
        "source_manifest_sha256": sha(MANIFEST),
        "late_id_audit_sha256": sha(AUDIT), "historic_result_reclassified": False}
    with OUTPUT.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": value["status"],
        "phases": [{"phase": row["phase"],
                    "critical": row["critical_business_or_control_late_spans"]}
                   for row in phases]}))


if __name__ == "__main__":
    main()
