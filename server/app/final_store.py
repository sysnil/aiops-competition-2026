from __future__ import annotations

import json
import hashlib
import os
import re
import sqlite3
import stat
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .accuracy_speed import decide_accuracy_speed_round
from .storage import StorageError


TASK_TRANSITIONS = {
    "NOT_OPEN": "OPEN", "OPEN": "SUBMISSION_LOCKED",
    "SUBMISSION_LOCKED": "SCORED", "SCORED": "CLOSED",
}
ROUND_TRANSITIONS = {
    "NOT_OPEN": "READY", "READY": "RUNNING", "RUNNING": "SUBMISSION_LOCKED",
    "SUBMISSION_LOCKED": "JUDGING", "JUDGING": "RESULT_CONFIRMED",
    "RESULT_CONFIRMED": "CLOSED",
}

FINAL_DISPLAY_MULTIPLIER = 10


def final_display_score(raw_score: float) -> float:
    return round(raw_score * FINAL_DISPLAY_MULTIPLIER, 2)


def archive_final_answer(directory: Path, submission_id: str, digest: str, answer: dict[str, Any]) -> Path:
    if not re.fullmatch(r"FSUB-[A-Z0-9]{8,64}", submission_id):
        raise ValueError("invalid Final submission ID")
    canonical = json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != digest:
        raise ValueError("Final answer digest mismatch")
    directory.mkdir(mode=0o750, parents=True, exist_ok=True)
    destination = directory / f"{submission_id}.json"
    if destination.exists() or destination.is_symlink():
        info = destination.lstat()
        if not stat.S_ISREG(info.st_mode) or destination.is_symlink():
            raise ValueError("Final answer archive is not a regular file")
        saved = json.loads(destination.read_text(encoding="utf-8"))
        if hashlib.sha256(json.dumps(saved, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest() != digest:
            raise ValueError("Final answer archive differs from the registered answer")
        return destination
    fd, temporary = tempfile.mkstemp(prefix=".final-answer-", dir=directory)
    try:
        os.fchmod(fd, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(answer, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            return archive_final_answer(directory, submission_id, digest, answer)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return destination
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone required")
    return result.astimezone(timezone.utc)


def final_window_visible(item: dict[str, Any], now: datetime) -> bool:
    """FT may explicitly have no deadline; every PK still requires a finite end."""
    try:
        if item["status"] not in {"OPEN", "RUNNING"}:
            return False
        opened = parse_time(item.get("opens_at"))
        closed = parse_time(item.get("closes_at"))
        if not opened or now < opened:
            return False
        return (item.get("kind") == "FT") if closed is None else now < closed
    except (TypeError, ValueError, KeyError):
        return False


class FinalStore:
    """Persistent Final-only state in separate tables of the existing grading DB."""

    def __init__(self, database: Path, catalog: Path):
        self.database = database
        self.catalog = catalog
        self.policy = json.loads(catalog.read_text(encoding="utf-8")).get("preview_scoring", {})
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        catalog = json.loads(self.catalog.read_text(encoding="utf-8"))
        if catalog.get("publication_state") != "INTERNAL_DEVELOPMENT_ONLY":
            raise ValueError("final catalog must remain internal during Stage 1")
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS final_tasks (
                    task_id TEXT PRIMARY KEY, case_id TEXT, kind TEXT NOT NULL,
                    status TEXT NOT NULL, opens_at TEXT, closes_at TEXT,
                    max_submissions INTEGER NOT NULL, simulation_only INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS final_rounds (
                    round_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL, champion_team_id TEXT, challenger_team_id TEXT,
                    winner_team_id TEXT, opens_at TEXT, closes_at TEXT,
                    simulation_only INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(task_id) REFERENCES final_tasks(task_id)
                );
                CREATE TABLE IF NOT EXISTS final_submissions (
                    submission_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                    team_id TEXT NOT NULL, case_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, answer_sha256 TEXT NOT NULL,
                    answer_json TEXT NOT NULL, received_at TEXT NOT NULL,
                    status TEXT NOT NULL, grade_json TEXT,
                    grading_mode TEXT NOT NULL DEFAULT 'OFFICIAL',
                    UNIQUE(team_id, task_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS final_submissions_by_team
                    ON final_submissions(team_id, task_id, received_at);
                CREATE TABLE IF NOT EXISTS final_judges (
                    round_id TEXT NOT NULL, team_id TEXT NOT NULL, judge_id TEXT NOT NULL,
                    score REAL NOT NULL, notes TEXT, updated_at TEXT NOT NULL,
                    PRIMARY KEY(round_id, team_id, judge_id)
                );
                CREATE TABLE IF NOT EXISTS final_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                    actor TEXT NOT NULL, action TEXT NOT NULL, object_id TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(final_submissions)")}
            if "grading_mode" not in columns:
                db.execute("ALTER TABLE final_submissions ADD COLUMN grading_mode TEXT NOT NULL DEFAULT 'OFFICIAL'")
            for item in catalog["tasks"]:
                db.execute("INSERT OR IGNORE INTO final_tasks VALUES (?,?,?,?,?,?,?,?)", (
                    item["task_id"], item.get("case_id"), item["kind"],
                    "NOT_OPEN", None, None, int(item.get("max_submissions", 1)),
                    int(bool(item.get("simulation_only", False))),
                ))
                if item["kind"] == "PK":
                    db.execute("INSERT OR IGNORE INTO final_rounds VALUES (?,?,?,?,?,?,?,?,?)", (
                        item["task_id"], item["task_id"], "NOT_OPEN", None, None,
                        None, None, None, int(bool(item.get("simulation_only", False))),
                    ))

    @staticmethod
    def _audit(db: sqlite3.Connection, actor: str, action: str, object_id: str, details: dict[str, Any]) -> None:
        db.execute("INSERT INTO final_audit(at,actor,action,object_id,details_json) VALUES (?,?,?,?,?)", (
            utc_now(), actor, action, object_id,
            json.dumps(details, ensure_ascii=False, sort_keys=True),
        ))

    def task(self, task_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM final_tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise StorageError("TASK_NOT_FOUND", "task does not exist or is not available", 404)
        return dict(row)

    def tasks(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM final_tasks ORDER BY task_id")]

    def round(self, round_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM final_rounds WHERE round_id=?", (round_id,)).fetchone()
        if not row:
            raise StorageError("ROUND_NOT_FOUND", "round does not exist or is not available", 404)
        return dict(row)

    def rounds(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM final_rounds ORDER BY round_id")]

    def transition_task(self, task_id: str, target: str, actor: str, duration_seconds: int | None = None) -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            item = db.execute("SELECT * FROM final_tasks WHERE task_id=?", (task_id,)).fetchone()
            if not item or item["kind"] != "FT" or TASK_TRANSITIONS.get(item["status"]) != target:
                raise StorageError("INVALID_TRANSITION", "task transition is not allowed", 409)
            if target == "OPEN":
                if not item["case_id"] or not duration_seconds or not 60 <= duration_seconds <= 14400:
                    raise StorageError("INVALID_SCHEDULE", "case and bounded duration are required", 400)
                opens = datetime.now(timezone.utc)
                closes = opens + timedelta(seconds=duration_seconds)
                db.execute("UPDATE final_tasks SET status=?,opens_at=?,closes_at=? WHERE task_id=?", (target, opens.isoformat(), closes.isoformat(), task_id))
            else:
                if target == "SCORED":
                    rows = db.execute("SELECT status FROM final_submissions WHERE task_id=?", (task_id,)).fetchall()
                    if any(row[0] in {"RECEIVED", "VALIDATING", "GRADING"} for row in rows):
                        raise StorageError("GRADING_IN_PROGRESS", "submissions are still being graded", 409)
                db.execute("UPDATE final_tasks SET status=? WHERE task_id=?", (target, task_id))
            self._audit(db, actor, "TASK_" + target, task_id, {"previous": item["status"]})
        return self.task(task_id)

    def configure_round(self, round_id: str, champion: str, challenger: str, actor: str) -> dict[str, Any]:
        if not champion or not challenger or champion == challenger:
            raise StorageError("INVALID_PARTICIPANTS", "two different teams are required", 400)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            item = db.execute("SELECT * FROM final_rounds WHERE round_id=?", (round_id,)).fetchone()
            task = db.execute("SELECT * FROM final_tasks WHERE task_id=?", (round_id,)).fetchone()
            if not item or not task or item["status"] != "NOT_OPEN" or not task["case_id"]:
                raise StorageError("ROUND_NOT_READY", "round cannot be configured", 409)
            previous_id = f"PK-R{int(round_id[-2:]) - 1:02d}"
            if round_id != "PK-R01":
                previous = db.execute("SELECT * FROM final_rounds WHERE round_id=?", (previous_id,)).fetchone()
                if not previous or previous["status"] not in {"RESULT_CONFIRMED", "CLOSED"} or previous["winner_team_id"] != champion:
                    raise StorageError("CHAMPION_MISMATCH", "champion must be the previous winner", 409)
            used = db.execute("SELECT t.case_id FROM final_rounds r JOIN final_tasks t ON t.task_id=r.task_id WHERE r.status!='NOT_OPEN' AND r.round_id!=?", (round_id,)).fetchall()
            if not item["simulation_only"] and task["case_id"] in {row[0] for row in used}:
                raise StorageError("CASE_REUSE_FORBIDDEN", "formal PK case has already been used", 409)
            db.execute("UPDATE final_rounds SET status='READY',champion_team_id=?,challenger_team_id=? WHERE round_id=?", (champion, challenger, round_id))
            self._audit(db, actor, "ROUND_READY", round_id, {"champion": champion, "challenger": challenger})
        return self.round(round_id)

    def assign_round_case(self, round_id: str, case_id: str, actor: str, *, simulation_only: bool = False) -> dict[str, Any]:
        if not case_id.startswith("SF-") or len(case_id) != 6:
            raise StorageError("INVALID_CASE", "invalid case identifier", 400)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            round_item = db.execute("SELECT * FROM final_rounds WHERE round_id=?", (round_id,)).fetchone()
            task = db.execute("SELECT * FROM final_tasks WHERE task_id=?", (round_id,)).fetchone()
            if not round_item or not task or round_item["status"] != "NOT_OPEN" or task["case_id"]:
                raise StorageError("CASE_ASSIGNMENT_CLOSED", "round case cannot be changed", 409)
            if not simulation_only:
                raise StorageError("AWAITING_CASE", "formal case requires separate approved registration", 409)
            db.execute("UPDATE final_tasks SET case_id=?,simulation_only=1 WHERE task_id=?", (case_id, round_id))
            db.execute("UPDATE final_rounds SET simulation_only=1 WHERE round_id=?", (round_id,))
            self._audit(db, actor, "SIMULATION_CASE_ASSIGNED", round_id, {"case_id": case_id})
        return self.round(round_id)

    def transition_round(self, round_id: str, target: str, actor: str, duration_seconds: int | None = None) -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            item = db.execute("SELECT * FROM final_rounds WHERE round_id=?", (round_id,)).fetchone()
            if not item or ROUND_TRANSITIONS.get(item["status"]) != target or target == "READY":
                raise StorageError("INVALID_TRANSITION", "round transition is not allowed", 409)
            if target == "JUDGING" and self.policy.get("round_winner_rule") == "ACCURACY_THEN_SERVER_RECEIPT_V1":
                raise StorageError("JUDGING_DISABLED", "this batch uses automatic round results", 409)
            if target == "RUNNING":
                if not duration_seconds or not 60 <= duration_seconds <= 14400:
                    raise StorageError("INVALID_SCHEDULE", "bounded duration is required", 400)
                opens = datetime.now(timezone.utc)
                db.execute("UPDATE final_rounds SET status=?,opens_at=?,closes_at=? WHERE round_id=?", (target, opens.isoformat(), (opens + timedelta(seconds=duration_seconds)).isoformat(), round_id))
            else:
                db.execute("UPDATE final_rounds SET status=? WHERE round_id=?", (target, round_id))
            self._audit(db, actor, "ROUND_" + target, round_id, {"previous": item["status"]})
        return self.round(round_id)

    def create_submission(
        self,
        task_id: str,
        team_id: str,
        key: str,
        digest: str,
        answer: dict[str, Any],
        *,
        preview: bool,
        grading_mode: str | None = None,
        preview_submission_limit: int | None = None,
    ) -> dict[str, Any]:
        mode = grading_mode or ("PREVIEW" if preview else "OFFICIAL")
        if mode not in {"OFFICIAL", "PREVIEW", "INTERNAL_PREVIEW", "HYBRID_INTERNAL_PREVIEW"}:
            raise StorageError("INVALID_GRADING_MODE", "grading mode is not allowed", 400)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT * FROM final_tasks WHERE task_id=?", (task_id,)).fetchone()
            if not task or not task["case_id"] or answer.get("case_id") != task["case_id"]:
                raise StorageError("TASK_NOT_FOUND", "task is not available", 404)
            current = None
            if task['kind'] == 'PK' and mode == 'OFFICIAL' and not preview:
                current = db.execute('SELECT * FROM final_rounds WHERE round_id=?', (task_id,)).fetchone()
                # Scope transport retries to this opening. Old answers remain immutable.
                original_key = key
                opening = current['opens_at'] if current else None
                key = 'pk-session-' + hashlib.sha256(((opening or '') + '\0' + key).encode()).hexdigest()
                existing = db.execute(
                    'SELECT * FROM final_submissions WHERE task_id=? AND team_id=? '
                    'AND idempotency_key IN (?,?) AND received_at>=? AND received_at<?',
                    (task_id, team_id, key, original_key, opening, current['closes_at'] if current else None),
                ).fetchone()
            else:
                existing = db.execute("SELECT * FROM final_submissions WHERE task_id=? AND team_id=? AND idempotency_key=?", (task_id, team_id, key)).fetchone()
            if existing:
                if existing["answer_sha256"] != digest:
                    raise StorageError("IDEMPOTENCY_CONFLICT", "idempotency key already used", 409)
                archive_final_answer(self.database.parent / "final-answers", existing["submission_id"], digest, answer)
                return dict(existing)
            now = datetime.now(timezone.utc)
            if task["kind"] == "FT":
                allowed = final_window_visible(dict(task), now)
            else:
                current = db.execute("SELECT * FROM final_rounds WHERE round_id=?", (task_id,)).fetchone()
                allowed = bool(current and current["status"] == "RUNNING" and team_id in {current["champion_team_id"], current["challenger_team_id"]} and parse_time(current["opens_at"]) <= now < parse_time(current["closes_at"]))
            if not allowed and not preview:
                raise StorageError("SUBMISSION_CLOSED", "submission window is not open", 403)
            if current is not None and mode == 'OFFICIAL' and not preview:
                count = db.execute(
                    'SELECT count(*) FROM final_submissions WHERE task_id=? AND team_id=? AND grading_mode=? '
                    'AND received_at>=? AND received_at<?',
                    (task_id, team_id, mode, current['opens_at'], current['closes_at']),
                ).fetchone()[0]
            else:
                count = db.execute("SELECT count(*) FROM final_submissions WHERE task_id=? AND team_id=? AND grading_mode=?", (task_id, team_id, mode)).fetchone()[0]
            limit = task["max_submissions"]
            if preview and mode == "PREVIEW" and preview_submission_limit is not None:
                if not 1 <= preview_submission_limit <= 20:
                    raise StorageError("INVALID_PREVIEW_LIMIT", "preview submission limit is invalid", 400)
                limit = preview_submission_limit
            unlimited = task["kind"] == "FT" and limit == 0
            if not unlimited and count >= limit:
                raise StorageError("SUBMISSION_LIMIT_REACHED", "submission limit reached", 429)
            submission_id = "FSUB-" + uuid.uuid4().hex[:20].upper()
            db.execute("INSERT INTO final_submissions(submission_id,task_id,team_id,case_id,idempotency_key,answer_sha256,answer_json,received_at,status,grade_json,grading_mode) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                submission_id, task_id, team_id, task["case_id"], key, digest,
                json.dumps(answer, ensure_ascii=False, sort_keys=True), now.isoformat(), "RECEIVED", None,
                mode,
            ))
            archive_final_answer(self.database.parent / "final-answers", submission_id, digest, answer)
            self._audit(db, team_id, "SUBMISSION_RECEIVED", submission_id, {"task_id": task_id, "preview": preview})
        return self.submission(submission_id, team_id)

    def submission(self, submission_id: str, team_id: str | None = None) -> dict[str, Any]:
        with self._connect() as db:
            sql = "SELECT * FROM final_submissions WHERE submission_id=?"
            values = [submission_id]
            if team_id:
                sql += " AND team_id=?"
                values.append(team_id)
            row = db.execute(sql, values).fetchone()
        if not row:
            raise StorageError("SUBMISSION_NOT_FOUND", "submission does not exist", 404)
        return dict(row)

    def set_grade(self, submission_id: str, result: dict[str, Any], actor: str = "private-grader") -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM final_submissions WHERE submission_id=?", (submission_id,)).fetchone()
            if not row:
                raise StorageError("SUBMISSION_NOT_FOUND", "submission does not exist", 404)
            if row["grade_json"]:
                return dict(row)
            status = str(result.get("status", "GRADE_PENDING"))
            db.execute("UPDATE final_submissions SET status=?,grade_json=? WHERE submission_id=?", (status, json.dumps(result, ensure_ascii=False, sort_keys=True), submission_id))
            self._audit(db, actor, "SUBMISSION_GRADED", submission_id, {"status": status, "task_id": row["task_id"]})
        return self.submission(submission_id)

    def claim_for_grading(self, submission_id: str) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,grade_json FROM final_submissions WHERE submission_id=?", (submission_id,)).fetchone()
            if not row or row["grade_json"] or row["status"] not in {"RECEIVED", "GRADE_PENDING"}:
                return False
            db.execute("UPDATE final_submissions SET status='GRADING' WHERE submission_id=?", (submission_id,))
            self._audit(db, "final-worker", "SUBMISSION_GRADE_CLAIMED", submission_id, {})
            return True

    def recover_interrupted_grading(self) -> int:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT submission_id FROM final_submissions WHERE status='GRADING' AND grade_json IS NULL").fetchall()
            for row in rows:
                db.execute("UPDATE final_submissions SET status='GRADE_PENDING' WHERE submission_id=?", (row[0],))
                self._audit(db, "final-worker", "SUBMISSION_GRADE_RECOVERED", row[0], {"reason": "PROCESS_RESTART"})
            return len(rows)

    def mark_pending(self, submission_id: str, reason: str) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM final_submissions WHERE submission_id=?", (submission_id,)).fetchone()
            if not row or row["status"] not in {"RECEIVED", "GRADE_PENDING", "GRADING"}:
                return
            db.execute("UPDATE final_submissions SET status='GRADE_PENDING' WHERE submission_id=?", (submission_id,))
            self._audit(db, "final-worker", "SUBMISSION_GRADE_PENDING", submission_id, {"reason": reason})

    def pending_ids(self) -> list[str]:
        with self._connect() as db:
            return [row[0] for row in db.execute("SELECT submission_id FROM final_submissions WHERE status IN ('RECEIVED','GRADE_PENDING') ORDER BY received_at")]

    def judge(self, round_id: str, team_id: str, judge_id: str, score: float, notes: str, actor: str) -> None:
        if self.policy.get("round_winner_rule") == "ACCURACY_THEN_SERVER_RECEIPT_V1":
            raise StorageError("JUDGING_DISABLED", "this batch uses automatic round results", 409)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            item = db.execute("SELECT * FROM final_rounds WHERE round_id=?", (round_id,)).fetchone()
            if not item or item["status"] != "JUDGING" or team_id not in {item["champion_team_id"], item["challenger_team_id"]}:
                raise StorageError("JUDGING_NOT_OPEN", "round is not judging", 409)
            if not 0 <= score <= 100:
                raise StorageError("INVALID_SCORE", "judge score must be 0 to 100", 400)
            previous = db.execute("SELECT score FROM final_judges WHERE round_id=? AND team_id=? AND judge_id=?", (round_id, team_id, judge_id)).fetchone()
            db.execute("INSERT INTO final_judges VALUES (?,?,?,?,?,?) ON CONFLICT(round_id,team_id,judge_id) DO UPDATE SET score=excluded.score,notes=excluded.notes,updated_at=excluded.updated_at", (round_id, team_id, judge_id, score, notes, utc_now()))
            self._audit(db, actor, "JUDGE_SCORE_SET", round_id, {"team_id": team_id, "judge_id": judge_id, "previous": previous[0] if previous else None, "score": score})

    def _round_totals(self, db: sqlite3.Connection, item: sqlite3.Row) -> dict[str, float]:
        if not self.policy or (
            not item["simulation_only"]
            and not bool(self.policy.get("internal_preview_only"))
        ):
            raise StorageError("FORMAL_WEIGHTS_NOT_LOCKED", "official round scoring is not enabled", 409)
        totals = {}
        for team_id in (item["champion_team_id"], item["challenger_team_id"]):
            judges = db.execute("SELECT score FROM final_judges WHERE round_id=? AND team_id=?", (item["round_id"], team_id)).fetchall()
            if not judges:
                raise StorageError("JUDGE_SCORE_MISSING", "both teams need judge scores", 409)
            base = 0.0
            for task_id in ("FT-01", "FT-02", "FT-03"):
                row = db.execute("SELECT grade_json FROM final_submissions WHERE task_id=? AND team_id=? ORDER BY received_at DESC LIMIT 1", (task_id, team_id)).fetchone()
                grade = json.loads(row[0]) if row and row[0] else {}
                if grade.get("status") == "PREVIEW_COMPLETED":
                    base += float(grade.get("score") or 0)
            row = db.execute("SELECT grade_json FROM final_submissions WHERE task_id=? AND team_id=? ORDER BY received_at DESC LIMIT 1", (item["round_id"], team_id)).fetchone()
            grade = json.loads(row[0]) if row and row[0] else {}
            auto = float(grade.get("score") or 0) if grade.get("status") == "PREVIEW_COMPLETED" else 0.0
            judge_average = sum(float(judge[0]) for judge in judges) / len(judges)
            base_component = base / 30 * float(self.policy["base_max"])
            auto_component = auto / 10 * float(self.policy["pk_auto_max"])
            judge_component = judge_average / 100 * float(self.policy["judge_max"])
            totals[team_id] = round(base_component + auto_component + judge_component, 3)
        return totals

    def confirm_round(self, round_id: str, actor: str) -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            item = db.execute("SELECT * FROM final_rounds WHERE round_id=?", (round_id,)).fetchone()
            accuracy_speed = self.policy.get("round_winner_rule") == "ACCURACY_THEN_SERVER_RECEIPT_V1"
            expected_status = "SUBMISSION_LOCKED" if accuracy_speed else "JUDGING"
            if not item or item["status"] != expected_status:
                raise StorageError("ROUND_NOT_READY", "round cannot be confirmed", 409)
            if accuracy_speed:
                participants = (item["champion_team_id"], item["challenger_team_id"])
                submissions = {}
                for team in participants:
                    row = db.execute(
                        "SELECT * FROM final_submissions WHERE task_id=? AND team_id=? AND grading_mode=? "
                        "AND received_at>=? AND received_at<? ORDER BY received_at, submission_id LIMIT 1",
                        (round_id, team, "INTERNAL_PREVIEW" if item["simulation_only"] else "OFFICIAL",
                         item['opens_at'], item['closes_at']),
                    ).fetchone()
                    submissions[team] = dict(row) if row else None
                try:
                    decision = decide_accuracy_speed_round(participants, submissions, item["opens_at"], item["closes_at"])
                except ValueError as exc:
                    raise StorageError("ROUND_RESULT_UNAVAILABLE", str(exc), 409) from exc
                winner = decision["winner"]
                if not winner:
                    if any(entry['valid_scored_submission'] for entry in decision['entries'].values()):
                        raise StorageError("ROUND_TIE_UNRESOLVED", "no faster valid submission exists", 409)
                    decision['decision_basis'] = 'NO_VALID_SUBMISSIONS_ZERO_SCORE'
                    decision['outcome'] = 'CLOSED_WITHOUT_WINNER'
                details = decision
            else:
                totals = self._round_totals(db, item)
                winner = max((item["champion_team_id"], item["challenger_team_id"]), key=lambda team: (totals[team], team == item["champion_team_id"]))
                details = {"winner": winner, "totals": totals}
            terminal = 'RESULT_CONFIRMED' if winner else 'CLOSED'
            db.execute("UPDATE final_rounds SET status=?,winner_team_id=? WHERE round_id=?", (terminal, winner, round_id))
            details['session_opens_at'] = item['opens_at']
            details['session_closes_at'] = item['closes_at']
            self._audit(db, actor, "ROUND_RESULT_CONFIRMED", round_id, details)
        return self.round(round_id)

    def qualification_summary(self, team_id: str, *, grading_mode: str | None = None) -> dict[str, Any]:
        tasks: list[dict[str, Any]] = []
        total = 0.0
        scored_count = 0
        with self._connect() as db:
            for task_id in ("FT-01", "FT-02", "FT-03"):
                sql = ("SELECT submission_id,status,grade_json,grading_mode FROM final_submissions "
                       "WHERE task_id=? AND team_id=?")
                values = [task_id, team_id]
                if grading_mode is not None:
                    sql += " AND grading_mode=?"
                    values.append(grading_mode)
                row = db.execute(sql + " ORDER BY received_at DESC LIMIT 1", values).fetchone()
                grade = json.loads(row["grade_json"]) if row and row["grade_json"] else None
                completed_status = "COMPLETED" if row and row["grading_mode"] == "OFFICIAL" else "PREVIEW_COMPLETED"
                raw_score = float(grade.get("score") or 0) if grade and grade.get("status") == completed_status else None
                score = final_display_score(raw_score) if raw_score is not None else None
                if score is not None:
                    total += score
                    scored_count += 1
                tasks.append({
                    "task_id": task_id,
                    "submission_id": row["submission_id"] if row else None,
                    "status": row["status"] if row else "NOT_SUBMITTED",
                    "score": score,
                    "raw_score": raw_score,
                    "grading_mode": row["grading_mode"] if row else None,
                })
        return {"team_id": team_id, "tasks": tasks, "total_score": round(total / 3, 2),
                "max_score": 100.0, "scored_count": scored_count, "aggregation": "THREE_TASK_MEAN"}

    def confirmed_result(self, round_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT a.details_json,a.at FROM final_audit a JOIN final_rounds r ON r.round_id=a.object_id "
                "WHERE a.action='ROUND_RESULT_CONFIRMED' AND a.object_id=? "
                "AND (r.opens_at IS NULL OR a.at>=r.opens_at) ORDER BY a.id DESC LIMIT 1",
                (round_id,),
            ).fetchone()
        if not row:
            return None
        result = json.loads(row["details_json"])
        result["confirmed_at"] = row["at"]
        return result

    def audit(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM final_audit ORDER BY id DESC LIMIT ?", (limit,))]
