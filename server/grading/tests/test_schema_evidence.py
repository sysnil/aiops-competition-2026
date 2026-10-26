from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from grading.submission_api.evidence_verifier import EvidenceVerifier, record_digest
from grading.submission_api.models import AnswerV2, AnswerV3


ROOT = Path(__file__).resolve().parents[1]


def demo_answer():
    return json.loads((ROOT / "demo/demo-answer.json").read_text(encoding="utf-8"))


def demo_answer_v3():
    return json.loads((ROOT / "demo/demo-answer-v3.json").read_text(encoding="utf-8"))


class SchemaTests(unittest.TestCase):
    def test_valid_demo_answer(self):
        self.assertEqual("2.0", AnswerV2.model_validate(demo_answer()).schema_version)

    def test_invalid_fault_type(self):
        value = demo_answer()
        value["anomalies"][0]["suspected_fault_type"] = "PretendFault"
        with self.assertRaises(ValidationError):
            AnswerV2.model_validate(value)

    def test_valid_v3_answer(self):
        self.assertEqual("3.0", AnswerV3.model_validate(demo_answer_v3()).schema_version)

    def test_invalid_anomaly_type(self):
        value = demo_answer_v3()
        value["anomalies"][0]["anomaly_type"] = "ServicePause"
        with self.assertRaises(ValidationError):
            AnswerV3.model_validate(value)

    def test_missing_anomaly_description(self):
        value = demo_answer_v3()
        value["anomalies"][0].pop("anomaly_description")
        with self.assertRaises(ValidationError):
            AnswerV3.model_validate(value)

    def test_old_fault_field_is_not_accepted_by_v3(self):
        value = demo_answer_v3()
        value["anomalies"][0]["suspected_fault_type"] = value["anomalies"][0].pop("anomaly_type")
        with self.assertRaises(ValidationError):
            AnswerV3.model_validate(value)

    def test_reversed_time(self):
        value = demo_answer()
        value["anomalies"][0]["start_time"] = "2030-01-01T00:30:00Z"
        with self.assertRaises(ValidationError):
            AnswerV2.model_validate(value)

    def test_path_injection_entity_rejected(self):
        value = demo_answer()
        value["anomalies"][0]["entity_id"] = "../../etc/passwd"
        with self.assertRaises(ValidationError):
            AnswerV2.model_validate(value)


class DemoEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.verifier = EvidenceVerifier(object(), ROOT / "demo/evidence.json")

    async def test_real_demo_evidence(self):
        result = await self.verifier.validate_answer(demo_answer())
        self.assertTrue(result["valid"])

    async def test_real_demo_v3_evidence(self):
        result = await self.verifier.validate_answer(demo_answer_v3())
        self.assertTrue(result["valid"])

    async def test_forged_value(self):
        value = demo_answer()
        value["anomalies"][0]["evidence"][0]["record"]["body"] = "forged"
        result = await self.verifier.validate_answer(value)
        self.assertFalse(result["valid"])

    async def test_wrong_digest(self):
        value = demo_answer()
        value["anomalies"][0]["evidence"][0]["record_sha256"] = "0" * 64
        result = await self.verifier.validate_answer(value)
        self.assertFalse(result["valid"])

    async def test_event_outside_window(self):
        value = demo_answer()
        value["anomalies"][0]["end_time"] = "2030-01-02T00:00:00Z"
        result = await self.verifier.validate_answer(value)
        self.assertFalse(result["valid"])

    async def test_duplicate_evidence_is_rejected(self):
        value = demo_answer()
        value["anomalies"][0]["evidence"].append(
            copy.deepcopy(value["anomalies"][0]["evidence"][0])
        )
        result = await self.verifier.validate_answer(value)
        self.assertFalse(result["valid"])
        self.assertIn("DUPLICATE_EVIDENCE", {item["code"] for item in result["checks"]})


if __name__ == "__main__":
    unittest.main()
