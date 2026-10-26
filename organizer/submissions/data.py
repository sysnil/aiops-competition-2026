"""Read-only scoring evidence for the committee submission viewer."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import quote

from semifinal_results import rank_rows


DATABASE = Path(os.environ.get("SUBMISSION_REVIEW_DATABASE", "/var/lib/aiops-grading/submissions.sqlite3"))
ORDINARY_ANSWERS = Path(os.environ.get("SUBMISSION_REVIEW_ORDINARY_ANSWERS", "/var/lib/aiops-grading/answers"))
FINAL_ANSWERS = Path(os.environ.get("SUBMISSION_REVIEW_FINAL_ANSWERS", "/var/lib/aiops-grading/final-answers"))
SUBMISSION_ID = re.compile(r"(?:SUB|FSUB)-[A-Z0-9]{8,64}\Z")
SEMIFINAL_PLAN = Path(os.environ.get("SUBMISSION_REVIEW_SEMIFINAL_PLAN", "/etc/aiops-submission-review/semifinal-scoring-plan.json"))


def _database(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{quote(str(database.resolve()), safe='/')}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _grade_score(raw: str | None, status: str) -> tuple[float | None, str | None]:
    if not raw:
        return None, None
    try:
        grade = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None, "INVALID_GRADE_JSON"
    if not isinstance(grade, dict):
        return None, "INVALID_GRADE_JSON"
    score = grade.get("score")
    if status not in {"COMPLETED", "PREVIEW_COMPLETED"} or isinstance(score, bool) or not isinstance(score, (int, float)):
        return None, str(grade.get("status") or status)
    return (float(score) if math.isfinite(score) else None), str(grade.get("status") or status)


def load_rows(database: Path = DATABASE) -> list[dict[str, Any]]:
    with closing(_database(database)) as connection:
        ordinary = connection.execute("""
            SELECT submission_id,team_id,case_id,question_id,task_type,competition_mode,
                   grading_mode,received_at,updated_at,status,total_score,answer_sha256,
                   answer_path,breakdown_json,validation_json,grader_version,rubric_version,
                   gt_version_identifier,review_status,public_message
            FROM submissions ORDER BY received_at DESC
        """).fetchall()
        final = connection.execute("""
            SELECT submission_id,team_id,case_id,task_id,grading_mode,received_at,status,
                   answer_sha256,grade_json
            FROM final_submissions ORDER BY received_at DESC
        """).fetchall()
    rows: list[dict[str, Any]] = []
    for item in ordinary:
        row = dict(item)
        row["source"] = "SEMIFINAL" if row["competition_mode"] == "SEMIFINAL" else "PRACTICE" if row["competition_mode"] == "PRACTICE" else "LEGACY"
        row["task_id"] = row.get("question_id") or row.get("task_type") or row["case_id"]
        score = row.get("total_score")
        row["score"] = float(score) if score is not None and row["status"] in {"COMPLETED", "PREVIEW_COMPLETED"} else None
        row["display_score"] = row["score"]
        row["display_max_score"] = 10 if row["task_type"] == "SEMIFINAL_QUERY_VISUALIZATION" else 100
        row["grade_status"] = row["status"]
        rows.append(row)
    for item in final:
        row = dict(item)
        row["source"] = "FINAL"
        row["competition_mode"] = "FINAL"
        row["updated_at"] = row["received_at"]
        row["score"], row["grade_status"] = _grade_score(row.get("grade_json"), row["status"])
        row["display_score"] = (round(row["score"] * 10, 2)
                                if row["score"] is not None and 0 <= row["score"] <= 10 else None)
        row["display_max_score"] = 100
        grade = json.loads(row["grade_json"]) if row.get("grade_json") else {}
        row["rubric_version"] = grade.get("rubric_version")
        row["gt_version_identifier"] = grade.get("gt_version_identifier")
        rows.append(row)
    rows.sort(key=lambda row: (row["received_at"], row["submission_id"]), reverse=True)
    return rows


def load_semifinal_ranking(database: Path = DATABASE, plan_path: Path = SEMIFINAL_PLAN) -> dict[str, Any]:
    import time

    if time.time() - database.stat().st_mtime > 30:
        raise RuntimeError("scoring snapshot is stale")
    plan_raw = plan_path.read_bytes()
    plan = json.loads(plan_raw)
    if plan.get("status") != "APPROVED_FOR_SCORING":
        raise ValueError("semifinal scoring plan is not approved")
    with closing(_database(database)) as connection:
        rows = [dict(row) for row in connection.execute("SELECT * FROM submissions ORDER BY submission_id")]
    result = rank_rows(rows, plan)
    result["plan_sha256"] = hashlib.sha256(plan_raw).hexdigest()
    result["plan"] = plan
    return result


def canonical_hash(answer: dict[str, Any]) -> str:
    payload = json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def answer_file(row: dict[str, Any], *, ordinary_root: Path = ORDINARY_ANSWERS,
                final_root: Path = FINAL_ANSWERS) -> Path:
    submission_id = str(row["submission_id"])
    if not SUBMISSION_ID.fullmatch(submission_id):
        raise ValueError("invalid submission ID")
    root = final_root if row["source"] == "FINAL" else ordinary_root
    expected = root / f"{submission_id}.json"
    if row["source"] != "FINAL" and Path(str(row["answer_path"])) != expected:
        raise ValueError("answer path differs from the approved archive root")
    if expected.is_symlink() or not expected.is_file():
        raise FileNotFoundError("submission JSON is missing or not a regular file")
    if not stat.S_ISREG(expected.stat().st_mode):
        raise ValueError("submission JSON is not a regular file")
    return expected


def read_answer(row: dict[str, Any], *, ordinary_root: Path = ORDINARY_ANSWERS,
                final_root: Path = FINAL_ANSWERS) -> dict[str, Any]:
    path = answer_file(row, ordinary_root=ordinary_root, final_root=final_root)
    if path.stat().st_size > 4_000_000:
        raise ValueError("submission JSON exceeds the viewer size limit")
    raw = path.read_bytes()
    answer = json.loads(raw)
    if not isinstance(answer, dict):
        raise ValueError("submission JSON is not an object")
    actual = canonical_hash(answer)
    return {"answer": answer, "raw": raw, "path": path,
            "file_sha256": hashlib.sha256(raw).hexdigest(),
            "canonical_sha256": actual,
            "verified": actual == row["answer_sha256"]}


def scoring_evidence(row: dict[str, Any]) -> dict[str, Any]:
    if row["source"] == "FINAL":
        return {"status": row["status"], "grading_mode": row["grading_mode"],
                "display_score": row["display_score"], "display_max_score": 100,
                "grade": json.loads(row["grade_json"]) if row.get("grade_json") else None}
    return {
        "status": row["status"], "grading_mode": row["grading_mode"],
        "score": row["score"], "grader_version": row.get("grader_version"),
        "rubric_version": row.get("rubric_version"), "gt_version_identifier": row.get("gt_version_identifier"),
        "review_status": row.get("review_status"), "public_message": row.get("public_message"),
        "breakdown": json.loads(row["breakdown_json"]) if row.get("breakdown_json") else None,
        "evidence_validation": json.loads(row["validation_json"]) if row.get("validation_json") else None,
    }


FINAL_QUALIFIER_PLAN = Path(os.environ.get('SUBMISSION_REVIEW_FINAL_QUALIFIER_PLAN', '/etc/aiops-submission-review/final-qualifier-scoring-plan.json'))


def load_final_qualifier_ranking(database: Path = DATABASE, plan_path: Path = FINAL_QUALIFIER_PLAN) -> dict[str, Any]:
    import time
    from final_qualifier_results import rank_rows as rank_final
    age = time.time() - database.stat().st_mtime
    if age > 30:
        raise RuntimeError('scoring snapshot is stale')
    plan_raw = plan_path.read_bytes()
    plan = json.loads(plan_raw)
    with closing(_database(database)) as connection:
        connection.execute('BEGIN')
        rows = [dict(row) for row in connection.execute('SELECT submission_id,team_id,task_id,case_id,received_at,status,grading_mode,grade_json,answer_sha256 FROM final_submissions ORDER BY submission_id')]
    result = rank_final(rows, plan)
    result['plan_sha256'] = hashlib.sha256(plan_raw).hexdigest()
    result['source_rows_sha256'] = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    result['snapshot_observed_age_seconds'] = max(0, age)
    result['plan'] = plan
    return result
