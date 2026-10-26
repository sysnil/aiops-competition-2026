#!/usr/bin/env python3
"""Verify and atomically import the frozen semifinal QA delivery.

The source bundles remain immutable.  Public question definitions are copied
into the reloadable catalog; only a minimal scoring projection is written to
the private preview GT directory.  Formal questions are never published by
this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


EXPECTED_MAPPING = {
    "EX-P01": "SF-001", "EX-P02": "SF-002",
    "Q01": "SF-003", "Q02": "SF-003", "Q03": "SF-003",
    "Q04": "SF-005", "Q05": "SF-005", "Q06": "SF-005",
    "Q07": "SF-010", "Q08": "SF-010", "Q09": "SF-010", "Q10": "SF-010",
}
PRIVATE_KEYS = {
    "three_gold_answers", "canonical_chart_data", "review_status",
    "grading_rubric", "source_public_manifest_sha256", "supporting_public_records",
}


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def verify_delivery(root: Path) -> dict[str, Any]:
    manifest = load(root / "DELIVERY_MANIFEST.json")
    files = manifest.get("files")
    if not isinstance(files, dict) or manifest.get("file_count") != len(files):
        raise ValueError(f"invalid delivery manifest inventory: {root}")
    failures: list[str] = []
    for relative, expected in files.items():
        path = root / relative
        if not path.is_file():
            failures.append(f"missing:{relative}")
            continue
        if path.stat().st_size != expected.get("size_bytes"):
            failures.append(f"size:{relative}")
        if digest(path) != expected.get("sha256"):
            failures.append(f"sha256:{relative}")
    if failures:
        raise ValueError(f"delivery verification failed for {root}: {failures}")
    source_manifest = root / "source-sha256-manifest.json"
    if source_manifest.is_file() and digest(source_manifest) != manifest.get("source_package_sha256_manifest"):
        raise ValueError("private source manifest SHA-256 mismatch")
    return manifest


def question_files(public_root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for folder in ("practice-examples", "questions"):
        for path in sorted((public_root / folder).glob("*.json")):
            item = load(path)
            question_id = str(item.get("question_id", ""))
            if question_id in result:
                raise ValueError(f"duplicate question: {question_id}")
            result[question_id] = path
    return result


def verify_questions(public_root: Path) -> dict[str, dict[str, Any]]:
    manifest = load(public_root / "question-manifest.json")
    paths = question_files(public_root)
    if set(paths) != set(EXPECTED_MAPPING):
        raise ValueError("question inventory does not contain the expected 12 IDs")
    declared = {
        row["question_id"]: row
        for section in ("formal_questions", "practice_examples")
        for row in manifest.get(section, [])
    }
    questions: dict[str, dict[str, Any]] = {}
    for question_id, case_id in EXPECTED_MAPPING.items():
        path = paths[question_id]
        item = load(path)
        if item.get("case_id") != case_id or declared.get(question_id, {}).get("case_id") != case_id:
            raise ValueError(f"case mapping mismatch for {question_id}")
        if digest(path) != declared.get(question_id, {}).get("question_sha256"):
            raise ValueError(f"question SHA-256 mismatch for {question_id}")
        if PRIVATE_KEYS.intersection(item):
            raise ValueError(f"private fields found in public question {question_id}")
        if len(item.get("questions", [])) != 3:
            raise ValueError(f"question {question_id} must contain exactly three prompts")
        item["mode"] = "practice" if question_id.startswith("EX-") else "semifinal"
        item["publication_status"] = "PRACTICE_PUBLISHED" if question_id.startswith("EX-") else "SEMIFINAL_PREVIEW"
        questions[question_id] = item
    return questions


def runtime_gt(raw: dict[str, Any], question: dict[str, Any]) -> dict[str, Any]:
    requirement = question["chart_requirement"]
    names = list(requirement["series"])
    points_by_name: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
    for point in raw["canonical_chart_data"]:
        name = str(point["series"])
        if name not in points_by_name:
            raise ValueError(f"unexpected GT series for {raw['question_id']}: {name}")
        points_by_name[name].append({
            "timestamp": point["timestamp_utc"],
            "value": point["value"],
        })
    answers = []
    for index, gold in enumerate(raw["three_gold_answers"], 1):
        item = {
            "question_index": index,
            "value": gold["answer"],
            "unit": gold["answer_unit"],
            "value_tolerance": gold.get("numerical_tolerance") or 0.0,
            "time_tolerance_seconds": gold.get("time_tolerance_seconds") or 0.0,
        }
        answers.append(item)
    return {
        "task_type": "SEMIFINAL_QUERY_VISUALIZATION",
        "question_id": raw["question_id"],
        "case_id": raw["case_id"],
        "gt_version": raw["gt_version"],
        "rubric_version": "semifinal-query-visualization-4+2+2+2-v1",
        "review_status": raw["review_status"],
        "classification": "DRAFT_PREVIEW_ONLY",
        "chart_value_tolerance": 1e-9,
        "chart_time_tolerance_seconds": 0.0,
        "chart": {
            "chart_type": requirement["type"],
            "title": question["title"],
            "x_axis": {"label": requirement["x_axis"], "timezone": "UTC"},
            "y_axis": {"label": requirement["y_axis"], "unit": requirement["y_axis"]},
            "series": [
                {"name": name, "unit": requirement["y_axis"], "points": points_by_name[name]}
                for name in names
            ],
        },
        "answers": answers,
        "generation": {
            "source_review_status": raw["review_status"],
            "source_public_manifest_sha256": raw["source_public_manifest_sha256"],
            "source_file_sha256": raw["source_file_sha256"],
        },
    }


def verify_gt(
    private_root: Path,
    questions: dict[str, dict[str, Any]],
    public_cases: Path,
    public_manifests: Path,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for question_id, case_id in EXPECTED_MAPPING.items():
        raw = load(private_root / "ground-truth" / f"{question_id}-gt.json")
        if raw.get("question_id") != question_id or raw.get("case_id") != case_id:
            raise ValueError(f"GT identity mismatch for {question_id}")
        if raw.get("review_status") != "DRAFT_PENDING_HUMAN_REVIEW":
            raise ValueError(f"GT must remain pending human review: {question_id}")
        if len(raw.get("three_gold_answers", [])) != 3:
            raise ValueError(f"GT answer count mismatch: {question_id}")
        source_name = questions[question_id]["query_spec"]["source_file"]
        delivered_source_manifest = private_root / "source-public-manifests" / f"{case_id}.json"
        if digest(delivered_source_manifest) != raw.get("source_public_manifest_sha256"):
            raise ValueError(f"delivered source Public manifest mismatch: {question_id}")
        source_manifest = load(delivered_source_manifest)
        if source_manifest.get("files", {}).get(source_name, {}).get("sha256") != raw.get("source_file_sha256"):
            raise ValueError(f"GT source file provenance mismatch: {question_id}")

        # The API import pipeline normalizes Parquet schemas, so its immutable
        # file hashes differ from the frozen source bundle.  Verify the full
        # provenance chain instead of incorrectly requiring byte identity.
        published_manifest = load(public_manifests / f"{case_id}.json")
        if published_manifest.get("source_manifest_sha256") != raw.get("source_public_manifest_sha256"):
            raise ValueError(f"published Case provenance mismatch: {question_id}")
        expected_window = questions[question_id]["time_range"]
        if (
            published_manifest.get("start_time") != expected_window["start_utc"]
            or published_manifest.get("end_time") != expected_window["end_exclusive_utc"]
        ):
            raise ValueError(f"published Case window mismatch: {question_id}")
        deployed_entry = next((item for item in published_manifest.get("files", []) if item.get("name") == source_name), None)
        deployed_path = public_cases / case_id / str(published_manifest.get("version", "public-v1")) / source_name
        if deployed_entry is None or not deployed_path.is_file() or digest(deployed_path) != deployed_entry.get("sha256"):
            raise ValueError(f"published normalized Public source mismatch: {question_id}")
        results[question_id] = runtime_gt(raw, questions[question_id])
    return results


def atomic_json(path: Path, payload: dict[str, Any], mode: int, uid: int | None = None, gid: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        if uid is not None and gid is not None:
            os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, default=Path("/data/aiops-contest/staging/semifinal-qa-v1/public"))
    parser.add_argument("--private-root", type=Path, default=Path("/etc/aiops-private-grader/import-staging/semifinal-qa-v1/private"))
    parser.add_argument("--catalog", type=Path, default=Path("/opt/aiops-contest-server/config/questions.json"))
    parser.add_argument("--preview-gt-dir", type=Path, default=Path("/etc/aiops-private-grader/qa-gt-preview"))
    parser.add_argument("--state", type=Path, default=Path("/etc/aiops-contest/competition_state.json"))
    parser.add_argument("--public-cases", type=Path, default=Path("/data/aiops-contest/public/cases"))
    parser.add_argument("--public-manifests", type=Path, default=Path("/data/aiops-contest/public/manifests"))
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    public_manifest = verify_delivery(args.public_root)
    private_manifest = verify_delivery(args.private_root)
    questions = verify_questions(args.public_root)
    runtime_gts = verify_gt(args.private_root, questions, args.public_cases, args.public_manifests)
    catalog = load(args.catalog)
    retained = [item for item in catalog.get("questions", []) if item.get("question_id") not in EXPECTED_MAPPING]
    catalog["questions"] = retained + [questions[key] for key in sorted(questions)]
    state = load(args.state)
    state.setdefault("semifinal", {})["internal_preview_cases"] = ["SF-003", "SF-005", "SF-010"]
    # Explicit invariants: importing never opens or publishes formal Cases.
    if state["semifinal"].get("published_cases"):
        raise ValueError("refusing import while formal published_cases is non-empty")
    if any(value.get("state") == "OPEN" for value in state["semifinal"].get("sessions", {}).values()):
        raise ValueError("refusing import while a semifinal session is OPEN")

    result = {
        "status": "VERIFIED" if not args.apply else "IMPORTED_PREVIEW_ONLY",
        "public_delivery_files": public_manifest["file_count"],
        "private_delivery_files": private_manifest["file_count"],
        "question_count": len(questions),
        "gt_count": len(runtime_gts),
        "formal_publication_status": "CLOSED_PREVIEW_ONLY",
        "gt_review_status": "DRAFT_PENDING_HUMAN_REVIEW",
        "mapping": EXPECTED_MAPPING,
    }
    if not args.apply:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.backup_dir is None:
        raise ValueError("--backup-dir is required with --apply")
    args.backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(args.catalog, args.backup_dir / "questions.json")
    shutil.copy2(args.state, args.backup_dir / "competition_state.json")
    shutil.copytree(args.preview_gt_dir, args.backup_dir / "qa-gt-preview")
    atomic_json(args.catalog, catalog, 0o644)
    grader_group = args.preview_gt_dir.stat().st_gid
    for question_id, payload in runtime_gts.items():
        atomic_json(args.preview_gt_dir / f"{question_id}.json", payload, 0o640, 0, grader_group)
    state_group = args.state.stat().st_gid
    atomic_json(args.state, state, 0o640, 0, state_group)
    record = args.public_root.parent / "import-result.json"
    atomic_json(record, result, 0o640)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
