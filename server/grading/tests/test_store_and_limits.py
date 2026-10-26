from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from grading.submission_api.models import AnswerV2, AnswerV3
from grading.submission_api.router import grading_mode_for, submission_schema_allowed
from grading.submission_api.store import SubmissionStore


ROOT = Path(__file__).resolve().parents[1]


def demo_answer():
    return json.loads((ROOT / "demo/demo-answer.json").read_text(encoding="utf-8"))


def demo_answer_v3():
    return json.loads((ROOT / "demo/demo-answer-v3.json").read_text(encoding="utf-8"))


class SchemaLimitTests(unittest.TestCase):
    def test_timezone_is_required(self):
        value = demo_answer()
        value["anomalies"][0]["start_time"] = "2030-01-01T00:20:00"
        with self.assertRaises(ValidationError):
            AnswerV2.model_validate(value)

    def test_forbidden_sql_field_is_rejected(self):
        value = demo_answer()
        value["anomalies"][0]["evidence"][0]["record"]["sql"] = "DROP TABLE submissions"
        with self.assertRaises(ValidationError):
            AnswerV2.model_validate(value)

    def test_oversized_record_is_rejected(self):
        value = demo_answer()
        value["anomalies"][0]["evidence"][0]["record"]["body"] = "x" * 17_000
        with self.assertRaises(ValidationError):
            AnswerV2.model_validate(value)

    def test_event_count_is_bounded(self):
        value = demo_answer()
        value["anomalies"] = [copy.deepcopy(value["anomalies"][0]) for _ in range(11)]
        with self.assertRaises(ValidationError):
            AnswerV2.model_validate(value)

    def test_v3_timezone_and_count_are_bounded(self):
        value = demo_answer_v3()
        value["anomalies"][0]["start_time"] = "2030-01-01T00:20:00"
        with self.assertRaises(ValidationError):
            AnswerV3.model_validate(value)
        value = demo_answer_v3()
        value["anomalies"] = [copy.deepcopy(value["anomalies"][0]) for _ in range(17)]
        with self.assertRaises(ValidationError):
            AnswerV3.model_validate(value)

    def test_v3_sixteen_events_are_accepted(self):
        value = demo_answer_v3()
        value["anomalies"] = [copy.deepcopy(value["anomalies"][0]) for _ in range(16)]
        self.assertEqual(16, len(AnswerV3.model_validate(value).anomalies))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = SubmissionStore(root / "submissions.sqlite3", root / "answers")

    def tearDown(self):
        self.temp.cleanup()

    def create(self, submission_id="SUB-TEST0000000000000001", team_id="TEAM-A", is_demo=False, grading_mode=None):
        self.store.create(
            {
                "submission_id": submission_id,
                "team_id": team_id,
                "case_id": "DEMO-001" if is_demo else "SF-001",
                "schema_version": "2.0",
                "idempotency_key": "key-1234567890123456",
                "answer_sha256": "a" * 64,
                "status": "RECEIVED",
                "is_demo": is_demo,
                "grading_mode": grading_mode or ("DEMO" if is_demo else "OFFICIAL"),
            },
            demo_answer(),
        )

    def test_team_isolation_is_part_of_lookup(self):
        self.create()
        self.assertIsNotNone(self.store.get("SUB-TEST0000000000000001", "TEAM-A"))
        self.assertIsNone(self.store.get("SUB-TEST0000000000000001", "TEAM-B"))

    def test_demo_is_excluded_from_official_limit(self):
        self.create(is_demo=True)
        self.assertEqual(0, self.store.official_count("TEAM-A", "DEMO-001"))

    def test_practice_preview_is_excluded_from_official_limit(self):
        self.create(grading_mode="PREVIEW")
        self.assertEqual(0, self.store.official_count("TEAM-A", "SF-001"))

    def test_practice_case_mode_is_independent_of_answer_schema(self):
        self.assertEqual("PREVIEW", grading_mode_for("SF-001"))
        self.assertEqual("PREVIEW", grading_mode_for("SF-002"))
        self.assertEqual("OFFICIAL", grading_mode_for("SF-003"))

    def test_new_practice_submission_requires_v3_without_rewriting_history(self):
        self.assertTrue(submission_schema_allowed("SF-001", "3.0"))
        self.assertFalse(submission_schema_allowed("SF-001", "2.0"))
        self.assertTrue(submission_schema_allowed("LEGACY-CASE", "2.0"))

    def test_waiting_judge_job_is_recoverable(self):
        self.create()
        self.store.update("SUB-TEST0000000000000001", status="WAITING_JUDGE", judge_attempts=1)
        self.assertEqual(["SUB-TEST0000000000000001"], self.store.unfinished_ids())

    def test_unfinished_jobs_can_be_recovered(self):
        self.create()
        self.assertEqual(["SUB-TEST0000000000000001"], self.store.unfinished_ids())
        self.store.update("SUB-TEST0000000000000001", status="PENDING_GT_APPROVAL")
        self.assertEqual([], self.store.unfinished_ids())


if __name__ == "__main__":
    unittest.main()
