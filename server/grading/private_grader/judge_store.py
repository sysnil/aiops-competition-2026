from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JudgeResultStore:
    """Private, event-level Judge cache used for idempotent partial retries."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS judge_event_results (
                    submission_id TEXT NOT NULL,
                    gt_version TEXT NOT NULL,
                    gt_event_id TEXT NOT NULL,
                    event_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    failure_code TEXT,
                    failure_message TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (submission_id, gt_version, gt_event_id, event_fingerprint)
                );
                CREATE INDEX IF NOT EXISTS judge_event_status
                    ON judge_event_results(status, updated_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def get(self, submission_id: str, gt_version: str, gt_event_id: str, fingerprint: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM judge_event_results WHERE submission_id=? AND gt_version=? "
                "AND gt_event_id=? AND event_fingerprint=?",
                (submission_id, gt_version, gt_event_id, fingerprint),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        if result.get("result_json"):
            result["result"] = json.loads(result["result_json"])
        return result

    def record_success(
        self, submission_id: str, gt_version: str, gt_event_id: str,
        fingerprint: str, result: dict[str, Any],
    ) -> None:
        self._upsert(submission_id, gt_version, gt_event_id, fingerprint, "COMPLETED", result, None, None)

    def record_failure(
        self, submission_id: str, gt_version: str, gt_event_id: str,
        fingerprint: str, code: str, message: str,
    ) -> None:
        self._upsert(submission_id, gt_version, gt_event_id, fingerprint, "FAILED", None, code, message[:500])

    def _upsert(
        self, submission_id: str, gt_version: str, gt_event_id: str, fingerprint: str,
        status: str, result: dict[str, Any] | None, failure_code: str | None,
        failure_message: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True) if result is not None else None
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO judge_event_results (
                    submission_id, gt_version, gt_event_id, event_fingerprint,
                    status, result_json, attempts, failure_code, failure_message, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(submission_id, gt_version, gt_event_id, event_fingerprint)
                DO UPDATE SET
                    status=excluded.status,
                    result_json=excluded.result_json,
                    attempts=judge_event_results.attempts + 1,
                    failure_code=excluded.failure_code,
                    failure_message=excluded.failure_message,
                    updated_at=excluded.updated_at
                """,
                (
                    submission_id, gt_version, gt_event_id, fingerprint, status,
                    encoded, failure_code, failure_message, now,
                ),
            )
