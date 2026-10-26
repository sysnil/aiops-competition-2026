"""Build a new immutable twelve-case Final GT set from reviewed public evidence."""

from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


OLD_CASES = ("SF-004", "SF-006", "SF-007", "SF-008", "SF-009", "SF-011", "SF-012")
NEW_CASES = ("SF-013", "SF-014", "SF-015", "SF-016", "SF-017")
VERSION = "FINAL-GT-TAXONOMY-20261020-v2"
TYPE_MAPPING = {
    ("MEMORY_WORKING_SET_INCREASE",): ("MEMORY_USAGE_INCREASE", ()),
    ("TRACE_ERROR_BURST", "TRACE_LATENCY_INCREASE"): (
        "REQUEST_ERROR_INCREASE", ("TRACE_LATENCY_INCREASE",)
    ),
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def raw_json(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def parse_time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone required")
    return result.astimezone(timezone.utc)


def build(old_root: Path, source_root: Path, public_root: Path,
          taxonomy_path: Path, schema_path: Path) -> tuple[dict, dict[str, bytes], bytes]:
    previous_raw = (old_root / "gt-manifest.json").read_bytes()
    previous = json.loads(previous_raw)
    if (previous.get("review_status") != "APPROVED" or
            {item["case_id"] for item in previous["cases"]} != set(OLD_CASES)):
        raise ValueError("previous approved GT set changed")
    schema = read_json(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    taxonomy_raw = taxonomy_path.read_bytes()
    taxonomy = {item["id"]: set(item["allowed_entity_types"])
                for item in read_json(taxonomy_path)["types"]}
    source_rows = []
    sources = {}
    for case_id in NEW_CASES:
        folder = source_root / case_id
        draft_path = folder / "anomaly-gt-draft.json"
        candidates_path = folder / "anomaly-candidates.json"
        draft = read_json(draft_path)
        candidates = read_json(candidates_path)
        manifest_path = public_root / "manifests" / f"{case_id}.json"
        public = read_json(manifest_path)
        source_sha = public.get("source_manifest_sha256")
        if (public.get("case_id") != case_id or public.get("publication_status") != "published" or
                draft.get("review_status") != "DRAFT_PENDING_HUMAN_REVIEW" or
                draft.get("formal_scoring_gt") is not False or
                candidates.get("formal_scoring_gt") is not False or
                draft.get("source_public_manifest_sha256") != source_sha or
                candidates.get("source_public_manifest_sha256") != source_sha):
            raise ValueError(f"new GT source/public binding failed: {case_id}")
        source_rows.append({
            "case_id": case_id,
            "draft_sha256": sha(draft_path.read_bytes()),
            "candidate_sha256": sha(candidates_path.read_bytes()),
            "source_public_manifest_sha256": source_sha,
            "published_manifest_sha256": sha(manifest_path.read_bytes()),
            "selected_event_ids": draft["scoring_event_ids"],
        })
        sources[case_id] = (draft, candidates, public)
    source_manifest_raw = raw_json({
        "classification": "PRIVATE_REVIEWED_SOURCE_PROVENANCE",
        "approval_source": "User confirmed review of SF-013 through SF-017 in this task on 2026-10-20",
        "cases": source_rows,
    })
    source_manifest_sha = sha(source_manifest_raw)

    outputs: dict[str, bytes] = {}
    entries = []
    for case_id in OLD_CASES:
        row = next(item for item in previous["cases"] if item["case_id"] == case_id)
        old_path = old_root / row["file"]
        if sha(old_path.read_bytes()) != row["sha256"]:
            raise ValueError(f"previous Case hash changed: {case_id}")
        data = read_json(old_path)
        data["version"] = VERSION
        for event in data["events"]:
            event["gt_version"] = VERSION
        validator.validate(data)
        outputs[case_id] = raw_json(data)

    for case_id in NEW_CASES:
        draft, candidates, public = sources[case_id]
        selected = draft.get("scoring_event_ids")
        merged = candidates.get("merged_events")
        if (not isinstance(selected, list) or not 1 <= len(selected) <= 5 or
                len(selected) != len(set(selected)) or not isinstance(merged, list)):
            raise ValueError(f"invalid selected events: {case_id}")
        by_id = {event["event_id"]: event for event in merged}
        if len(by_id) != len(merged) or not set(selected) <= set(by_id):
            raise ValueError(f"selected event identity mismatch: {case_id}")
        aliases: dict[str, set[str]] = {}
        for snapshot in public.get("public_topology", {}).get("snapshots", []):
            for pod in snapshot.get("pods", []):
                if pod.get("uid") and pod.get("name"):
                    aliases.setdefault(str(pod["uid"]), set()).add(str(pod["name"]))
        window_start = parse_time(public["start_time"])
        window_end = parse_time(public["end_time"])
        events = []
        for event_id in selected:
            event = by_id[event_id]
            kind = tuple(event.get("anomaly_types") or [])
            if kind not in TYPE_MAPPING:
                raise ValueError(f"unmapped anomaly category: {case_id}/{event_id}/{kind}")
            primary, accepted = TYPE_MAPPING[kind]
            entity_type = str(event.get("entity_type", "")).title()
            entity_id = str(event.get("entity_id") or "")
            if (entity_type not in taxonomy[primary] or
                    any(entity_type not in taxonomy[name] for name in accepted)):
                raise ValueError(f"invalid published type/entity: {case_id}/{event_id}")
            if entity_type == "Pod" and entity_id not in aliases:
                raise ValueError(f"Pod UID not present in public topology: {case_id}/{event_id}")
            if entity_type == "Service" and entity_id not in public.get("public_services", []):
                raise ValueError(f"Service not in public universe: {case_id}/{event_id}")
            start = parse_time(event["start_time"])
            end = parse_time(event["end_time"])
            if not window_start <= start <= end < window_end:
                raise ValueError(f"event outside public window: {case_id}/{event_id}")
            rubric = (
                "Public container_memory_working_set_bytes samples support the Pod memory increase."
                if primary == "MEMORY_USAGE_INCREASE" else
                "Public Trace error status and duration samples support the Service request error/latency event."
            )
            events.append({
                "gt_event_id": event_id,
                "anomaly_type": primary,
                "accepted_anomaly_types": list(accepted),
                "entity_type": entity_type,
                "entity_id": entity_id,
                "accepted_entity_aliases": sorted(aliases.get(entity_id, set())),
                "observable_start_time": event["start_time"],
                "observable_end_time": event["end_time"],
                "evidence_rubric": rubric,
                "review_status": "APPROVED",
                "gt_version": VERSION,
                "rubric_version": previous["scoring_rule_version"],
            })
        excluded = [{"event_id": event_id, "reason": "NOT_SELECTED_IN_USER_CONFIRMED_DRAFT"}
                    for event_id in sorted(set(by_id) - set(selected))]
        data = {
            "case_id": case_id,
            "task_type": "FINAL_ANOMALY_INVESTIGATION",
            "classification": "OFFICIAL",
            "status": "APPROVED",
            "review_status": "APPROVED",
            "version": VERSION,
            "rubric_version": previous["scoring_rule_version"],
            "matching_rule_version": previous["scoring_rule_version"],
            "time_tolerance_seconds": 120,
            "source_candidate_manifest_sha256": source_manifest_sha,
            "source_candidate_sha256": next(x["candidate_sha256"] for x in source_rows if x["case_id"] == case_id),
            "source_public_manifest_sha256": public["source_manifest_sha256"],
            "excluded_groups": excluded,
            "events": events,
        }
        validator.validate(data)
        outputs[case_id] = raw_json(data)

    for case_id, raw in sorted(outputs.items()):
        entries.append({"case_id": case_id, "file": f"{case_id}.json",
                        "sha256": sha(raw), "events": len(json.loads(raw)["events"])})
    manifest = {
        "version": VERSION,
        "classification": "PRIVATE_OFFICIAL_FINAL_GT",
        "review_status": "APPROVED",
        "approved_at_utc": datetime.now(timezone.utc).isoformat(),
        "approval": {
            "approval_source": "User confirmed SF-013 through SF-017 GT review in this task on 2026-10-20",
            "previous_approved_manifest_sha256": sha(previous_raw),
            "new_source_manifest_sha256": source_manifest_sha,
            "scope": "twelve Final Case GT definitions; no task/session publication",
            "unselected_candidates": "retained in private source, not represented as human REJECT decisions",
        },
        "taxonomy_sha256": sha(taxonomy_raw),
        "schema_sha256": sha(schema_path.read_bytes()),
        "scoring_rule_version": previous["scoring_rule_version"],
        "cases": entries,
    }
    return manifest, outputs, source_manifest_raw


def write_new(destination: Path, manifest: dict, outputs: dict[str, bytes], source_manifest: bytes) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    gid = grp.getgrnam("aiops-grader").gr_gid
    destination.mkdir(mode=0o750, parents=True)
    os.chown(destination, 0, gid)
    files = {f"{case_id}.json": raw for case_id, raw in outputs.items()}
    files["gt-manifest.json"] = raw_json(manifest)
    files["source-candidate-manifest.json"] = source_manifest
    for name, raw in files.items():
        path = destination / name
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o440)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(path, 0, gid)
    directory_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-root", type=Path, default=Path("/etc/aiops-private-grader/final-gt"))
    parser.add_argument("--source-root", type=Path, default=Path("/etc/aiops-private-grader/final-newcase-source-20261020"))
    parser.add_argument("--public-root", type=Path, default=Path("/data/aiops-contest/public"))
    parser.add_argument("--taxonomy", type=Path, default=Path("/opt/aiops-contest-server/grading/schemas/anomaly-taxonomy-v1.json"))
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--destination", type=Path, default=Path("/etc/aiops-private-grader/final-gt-v2-20261020"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    manifest, outputs, source = build(args.old_root, args.source_root, args.public_root,
                                      args.taxonomy, args.schema)
    if args.apply:
        write_new(args.destination, manifest, outputs, source)
    print(json.dumps({"status": "WRITTEN" if args.apply else "VALIDATED",
                      "manifest_sha256": sha(raw_json(manifest)),
                      "cases": len(outputs),
                      "events": sum(item["events"] for item in manifest["cases"]),
                      "new_case_events": {item["case_id"]: item["events"] for item in manifest["cases"]
                                          if item["case_id"] in NEW_CASES}}, sort_keys=True))


if __name__ == "__main__":
    main()
