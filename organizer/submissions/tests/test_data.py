from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from auth import verify_password
from data import _database, canonical_hash, load_rows, load_semifinal_ranking, read_answer
from snapshot import publish


def database(tmp_path: Path) -> tuple[Path, Path, Path]:
    db = tmp_path / "submissions.sqlite3"
    ordinary = tmp_path / "answers"
    final = tmp_path / "final-answers"
    ordinary.mkdir()
    final.mkdir()
    with sqlite3.connect(db) as connection:
        connection.executescript("""
            CREATE TABLE submissions (
                submission_id TEXT, team_id TEXT, case_id TEXT, question_id TEXT, task_type TEXT,
                competition_mode TEXT, grading_mode TEXT, received_at TEXT, updated_at TEXT,
                status TEXT, total_score REAL, answer_sha256 TEXT, answer_path TEXT,
                breakdown_json TEXT, validation_json TEXT, grader_version TEXT,
                rubric_version TEXT, gt_version_identifier TEXT, review_status TEXT, public_message TEXT
            );
            CREATE TABLE final_submissions (
                submission_id TEXT, team_id TEXT, case_id TEXT, task_id TEXT,
                grading_mode TEXT, received_at TEXT, status TEXT, answer_sha256 TEXT, grade_json TEXT
            );
        """)
    return db, ordinary, final


def test_load_all_modes_and_verify_real_answer_files(tmp_path):
    db, ordinary, final = database(tmp_path)
    practice = {"case_id": "SF-001", "question": "中文问题"}
    final_answer = {"case_id": "SF-004", "anomalies": []}
    practice_id = "SUB-12345678"
    final_id = "FSUB-12345678"
    (ordinary / f"{practice_id}.json").write_text(json.dumps(practice, ensure_ascii=False))
    (final / f"{final_id}.json").write_text(json.dumps(final_answer, ensure_ascii=False))
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            practice_id, "TEAM-01", "SF-001", "P-Q01", "SEMIFINAL_QUERY_VISUALIZATION",
            "PRACTICE", "PREVIEW", "2026-10-20T01:00:00Z", "2026-10-20T01:01:00Z",
            "PREVIEW_COMPLETED", 0, canonical_hash(practice), str(ordinary / f"{practice_id}.json"),
            "{}", "{}", "grader-v1", "rubric-v1", "gt-v1", "PREVIEW_ONLY", None,
        ))
        connection.execute("INSERT INTO final_submissions VALUES (?,?,?,?,?,?,?,?,?)", (
            final_id, "TEAM-02", "SF-004", "PK-R01", "OFFICIAL", "2026-10-20T02:00:00Z",
            "COMPLETED", canonical_hash(final_answer), json.dumps({"status": "COMPLETED", "score": 2.5}),
        ))
    rows = load_rows(db)
    assert [(row["source"], row["score"], row["display_score"], row["display_max_score"])
            for row in rows] == [("FINAL", 2.5, 25, 100), ("PRACTICE", 0.0, 0.0, 10)]
    assert read_answer(rows[0], ordinary_root=ordinary, final_root=final)["verified"]
    assert read_answer(rows[1], ordinary_root=ordinary, final_root=final)["verified"]


def test_wrong_path_and_changed_content_are_detected(tmp_path):
    db, ordinary, final = database(tmp_path)
    path = ordinary / "SUB-12345678.json"
    path.write_text('{"case_id":"SF-001","value":2}')
    row = {"submission_id": "SUB-12345678", "source": "PRACTICE",
           "answer_path": str(path), "answer_sha256": canonical_hash({"case_id": "SF-001", "value": 1})}
    assert not read_answer(row, ordinary_root=ordinary, final_root=final)["verified"]
    row["answer_path"] = str(tmp_path / "other.json")
    with pytest.raises(ValueError, match="approved archive root"):
        read_answer(row, ordinary_root=ordinary, final_root=final)


def test_review_password_hash(tmp_path):
    password = "committee-test-code"
    salt = bytes.fromhex("12" * 16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"algorithm": "pbkdf2_sha256", "iterations": 600_000,
                                "salt": salt.hex(), "digest": digest.hex()}))
    assert verify_password(password, auth)
    assert not verify_password("incorrect", auth)


def test_snapshot_includes_committed_wal_and_cannot_write(tmp_path):
    db, _, _ = database(tmp_path)
    snapshot = tmp_path / "snapshot" / "submissions.sqlite3"
    writer = sqlite3.connect(db)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO final_submissions(submission_id) VALUES ('wal-only')")
        writer.commit()
        assert db.with_name(db.name + "-wal").stat().st_size > 0
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        publish(db, snapshot)
        assert hashlib.sha256(db.read_bytes()).hexdigest() == before
        reader = _database(snapshot)
        try:
            assert reader.execute("SELECT submission_id FROM final_submissions").fetchall()[0][0] == "wal-only"
            assert reader.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                reader.execute("DELETE FROM final_submissions")
        finally:
            reader.close()
    finally:
        writer.close()


def test_snapshot_atomic_refresh_and_failed_publish_retains_previous(tmp_path):
    db, _, _ = database(tmp_path)
    snapshot = tmp_path / "snapshot" / "submissions.sqlite3"
    publish(db, snapshot)
    old_reader = _database(snapshot)
    assert old_reader.execute("SELECT count(*) FROM final_submissions").fetchone()[0] == 0
    try:
        with sqlite3.connect(db) as writer:
            writer.execute("INSERT INTO final_submissions(submission_id) VALUES ('new')")
        publish(db, snapshot)
        assert old_reader.execute("SELECT count(*) FROM final_submissions").fetchone()[0] == 0
        with sqlite3.connect(snapshot) as reader:
            assert reader.execute("SELECT count(*) FROM final_submissions").fetchone()[0] == 1
        valid = snapshot.read_bytes()
        invalid = tmp_path / "invalid.sqlite3"
        with sqlite3.connect(invalid) as writer:
            writer.execute("CREATE TABLE wrong(value)")
        with pytest.raises(RuntimeError, match="missing scoring tables"):
            publish(invalid, snapshot)
        assert snapshot.read_bytes() == valid
        assert not list(snapshot.parent.glob(".snapshot-*"))
    finally:
        old_reader.close()


def test_snapshot_rejects_live_destination(tmp_path):
    db, _, _ = database(tmp_path)
    with pytest.raises(ValueError, match="differ"):
        publish(db, db)


def test_formal_ranking_uses_official_time_window_and_rejects_stale_snapshot(tmp_path):
    import os
    import time

    db, _, _ = database(tmp_path)
    plan = {"version": "synthetic", "date": "2026-10-20", "timezone": "Asia/Shanghai", "status": "APPROVED_FOR_SCORING",
            "selection": "LATEST_COMPLETED_SCORED_SUBMISSION_WITHIN_WINDOW", "questions": [f"Q{i:02d}" for i in range(1,11)],
            "points_per_question": 10, "windows": {
                "AM": {"start":"13:30:00", "end":"15:30:00", "teams":[f"TEAM-{i:02d}" for i in range(1,10)]},
                "PM": {"start":"16:00:00", "end":"18:00:00", "teams":[f"TEAM-{i:02d}" for i in range(10,18)]}}}
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    with sqlite3.connect(db) as connection:
        connection.execute("ALTER TABLE submissions ADD COLUMN session TEXT")
        for sid, mode, when, score in [("official", "OFFICIAL", "2026-10-20T06:00:00Z", 7.5),
                                       ("preview", "PREVIEW", "2026-10-20T06:01:00Z", 10),
                                       ("late", "OFFICIAL", "2026-10-20T07:30:00Z", 10)]:
            connection.execute("INSERT INTO submissions(submission_id,team_id,question_id,task_type,competition_mode,grading_mode,received_at,status,total_score,session) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (sid,"TEAM-01","Q01","SEMIFINAL_QUERY_VISUALIZATION","SEMIFINAL",mode,when,"COMPLETED",score,"AM"))
    result = load_semifinal_ranking(db, plan_path)
    assert result["status"] == "COMPLETE"
    assert result["leaderboard"][0]["total_score"] == 7.5
    assert result["leaderboard"][0]["Q01_submission_id"] == "official"
    assert len(result["leaderboard"]) == 17
    os.utime(db, (time.time()-40, time.time()-40))
    with pytest.raises(RuntimeError, match="stale"):
        load_semifinal_ranking(db, plan_path)
