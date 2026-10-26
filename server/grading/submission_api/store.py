from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FINAL_STATUSES = {"COMPLETED", "PREVIEW_COMPLETED", "REJECTED", "FAILED", "PENDING_GT_APPROVAL", "PENDING_REVIEW"}


class SubmissionStore:
    def __init__(self, database_path: Path, submission_dir: Path):
        self.database_path = database_path
        self.submission_dir = submission_dir
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.submission_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS submissions (
                    submission_id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    answer_sha256 TEXT NOT NULL,
                    answer_path TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    is_demo INTEGER NOT NULL DEFAULT 0,
                    grading_mode TEXT NOT NULL DEFAULT 'OFFICIAL',
                    competition_mode TEXT NOT NULL DEFAULT 'LEGACY',
                    session TEXT NOT NULL DEFAULT 'UNASSIGNED',
                    token_id TEXT,
                    task_type TEXT,
                    question_id TEXT,
                    grader_version TEXT,
                    rubric_version TEXT,
                    gt_version_identifier TEXT,
                    llm_model_identifier TEXT,
                    breakdown_json TEXT,
                    penalty REAL,
                    total_score REAL,
                    review_status TEXT,
                    public_message TEXT,
                    validation_json TEXT,
                    judge_attempts INTEGER NOT NULL DEFAULT 0,
                    judge_progress_json TEXT,
                    judge_failure_code TEXT,
                    UNIQUE(team_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS submissions_team_case
                    ON submissions(team_id, case_id, received_at);
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(submissions)")}
            if "grading_mode" not in columns:
                connection.execute("ALTER TABLE submissions ADD COLUMN grading_mode TEXT NOT NULL DEFAULT 'OFFICIAL'")
                connection.execute("UPDATE submissions SET grading_mode=CASE WHEN is_demo=1 THEN 'DEMO' ELSE 'OFFICIAL' END")
            if "penalty" not in columns:
                connection.execute("ALTER TABLE submissions ADD COLUMN penalty REAL")
            if "judge_attempts" not in columns:
                connection.execute("ALTER TABLE submissions ADD COLUMN judge_attempts INTEGER NOT NULL DEFAULT 0")
            if "judge_progress_json" not in columns:
                connection.execute("ALTER TABLE submissions ADD COLUMN judge_progress_json TEXT")
            if "judge_failure_code" not in columns:
                connection.execute("ALTER TABLE submissions ADD COLUMN judge_failure_code TEXT")
            if "competition_mode" not in columns:
                connection.execute("ALTER TABLE submissions ADD COLUMN competition_mode TEXT NOT NULL DEFAULT 'LEGACY'")
            if "session" not in columns:
                connection.execute("ALTER TABLE submissions ADD COLUMN session TEXT NOT NULL DEFAULT 'UNASSIGNED'")
            if "token_id" not in columns:
                connection.execute("ALTER TABLE submissions ADD COLUMN token_id TEXT")
            if "task_type" not in columns:
                connection.execute("ALTER TABLE submissions ADD COLUMN task_type TEXT")
            if "question_id" not in columns:
                connection.execute("ALTER TABLE submissions ADD COLUMN question_id TEXT")

    def official_count(self, team_id: str, case_id: str) -> int:
        with self._connect() as connection:
            return int(connection.execute(
                "SELECT count(*) FROM submissions WHERE team_id=? AND case_id=? AND grading_mode='OFFICIAL'",
                (team_id, case_id),
            ).fetchone()[0])

    def qa_summary(self, team_id: str, competition_mode: str, *, scoring_plan: dict | None = None) -> dict[str, Any]:
        """Use official plan-scoped best scores; retain legacy practice summary."""
        if competition_mode.upper() == "SEMIFINAL" and scoring_plan is not None:
            from app.semifinal_ranking import rank_rows

            with self._connect() as connection:
                rows = [dict(row) for row in connection.execute(
                    "SELECT * FROM submissions WHERE team_id=? AND competition_mode='SEMIFINAL' ORDER BY submission_id",
                    (team_id,),
                )]
            result = rank_rows(rows, scoring_plan)
            entry = next(row for row in result["leaderboard"] if row["team_id"] == team_id)
            items = [{"question_id": question, "submission_id": entry[question+"_submission_id"],
                      "score": entry[question], "status": "COMPLETED", "received_at": entry[question+"_received_at_utc"],
                      "elapsed_seconds": entry[question+"_elapsed_seconds"]}
                     for question in scoring_plan["questions"] if entry[question+"_submission_id"] is not None]
            return {"items": items, "answered_questions": len(items), "total_score": entry["total_score"],
                    "maximum_score": entry["maximum_score"], "selection": result["selection"],
                    "ranking_version": result["ranking_version"], "total_score_elapsed_seconds": entry["total_score_elapsed_seconds"],
                    "total_score_elapsed_display": entry["total_score_elapsed_display"],
                    "total_score_achieved_at_utc": entry["total_score_achieved_at_utc"],
                    "pending_or_unresolved_submission_ids": result["pending_or_unresolved_submission_ids"],
                    "summary_status": result["session_summaries"][entry["session"]]["status"],
                    "scoring_date": scoring_plan["date"], "scoring_window": {k:scoring_plan["windows"][entry["session"]][k] for k in ("start","end")},
                    "scoring_timezone": scoring_plan["timezone"]}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM submissions
                WHERE team_id=? AND competition_mode=?
                  AND task_type='SEMIFINAL_QUERY_VISUALIZATION'
                  AND status IN ('COMPLETED', 'PREVIEW_COMPLETED')
                ORDER BY received_at DESC
                """,
                (team_id, competition_mode.upper()),
            ).fetchall()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = self._row(row)
            question_id = str(item.get("question_id") or "")
            if question_id and question_id not in latest:
                latest[question_id] = item
        items = [
            {
                "question_id": question_id,
                "submission_id": row["submission_id"],
                "score": float(row.get("total_score") or 0.0),
                "status": row["status"],
                "received_at": row["received_at"],
            }
            for question_id, row in sorted(latest.items())
        ]
        return {
            "items": items,
            "answered_questions": len(items),
            "total_score": round(sum(item["score"] for item in items), 3),
            "maximum_score": 100.0,
        }

    def unfinished_ids(self) -> list[str]:
        """Return jobs that can safely be retried after an API process restart."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT submission_id FROM submissions "
                "WHERE status IN ('RECEIVED', 'VALIDATING', 'GRADING', 'WAITING_JUDGE') "
                "ORDER BY received_at"
            ).fetchall()
        return [str(row[0]) for row in rows]

    def by_idempotency(self, team_id: str, key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM submissions WHERE team_id=? AND idempotency_key=?", (team_id, key)
            ).fetchone()
        return self._row(row) if row else None

    def create(self, record: dict[str, Any], answer: dict[str, Any]) -> None:
        answer_path = self.submission_dir / f"{record['submission_id']}.json"
        answer_path.write_text(json.dumps(answer, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        answer_path.chmod(0o640)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO submissions (
                    submission_id, team_id, case_id, schema_version, idempotency_key,
                    answer_sha256, answer_path, received_at, updated_at, status, is_demo, grading_mode,
                    competition_mode, session, token_id, task_type, question_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["submission_id"], record["team_id"], record["case_id"],
                    record["schema_version"], record["idempotency_key"], record["answer_sha256"],
                    str(answer_path), now, now, record["status"], int(record.get("is_demo", False)),
                    record.get("grading_mode", "DEMO" if record.get("is_demo", False) else "OFFICIAL"),
                    record.get("competition_mode", "LEGACY"), record.get("session", "UNASSIGNED"),
                    record.get("token_id"), record.get("task_type"), record.get("question_id"),
                ),
            )

    def update(self, submission_id: str, **fields: Any) -> None:
        allowed = {
            "status", "grader_version", "rubric_version", "gt_version_identifier",
            "llm_model_identifier", "breakdown_json", "total_score", "review_status",
            "public_message", "validation_json", "penalty",
            "judge_attempts", "judge_progress_json", "judge_failure_code",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        values["updated_at"] = datetime.now(timezone.utc).isoformat()
        sets = ", ".join(f"{key}=?" for key in values)
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE submissions SET {sets} WHERE submission_id=?",
                [*values.values(), submission_id],
            )

    def get(self, submission_id: str, team_id: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM submissions WHERE submission_id=?"
        params: list[Any] = [submission_id]
        if team_id is not None:
            sql += " AND team_id=?"
            params.append(team_id)
        with self._connect() as connection:
            row = connection.execute(sql, params).fetchone()
        return self._row(row) if row else None

    def answer(self, submission_id: str) -> dict[str, Any]:
        row = self.get(submission_id)
        if not row:
            raise KeyError(submission_id)
        return json.loads(Path(row["answer_path"]).read_text(encoding="utf-8"))

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key in ("breakdown_json", "validation_json", "judge_progress_json"):
            if result.get(key):
                result[key[:-5]] = json.loads(result[key])
        result["is_demo"] = bool(result["is_demo"])
        return result
