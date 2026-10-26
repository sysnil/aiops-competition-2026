from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from client.public_api import PublicAPI
from final_answer import build_final_answer
from main import final_task_active_status


class FinalModeTests(unittest.TestCase):
    def test_final_task_active_status_distinguishes_qualifier_and_pk(self) -> None:
        self.assertEqual(final_task_active_status("FT"), "OPEN")
        self.assertEqual(final_task_active_status("PK"), "RUNNING")

    def test_final_public_api_paths_are_task_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            api = PublicAPI("http://example.invalid", tmp, "test", mode="final", final_task_id="FT-01")
            try:
                self.assertEqual(api.competition_path("cases"), "/v1/final/tasks")
                self.assertEqual(
                    api.competition_path("cases/SF-006/metrics"),
                    "/v1/final/tasks/FT-01/case/metrics",
                )
                self.assertEqual(
                    api.competition_path("cases/SF-006/traces/abc"),
                    "/v1/final/tasks/FT-01/case/traces/abc",
                )
            finally:
                api.close()

    def test_final_answer_contains_real_evidence_chart_and_valid_schema(self) -> None:
        root = Path(__file__).resolve().parents[1]
        record = {
            "timestamp": "2026-10-17T07:20:00Z",
            "metric_name": "container_memory_working_set_bytes",
            "service": "frontend",
            "value": 123.5,
        }
        digest = hashlib.sha256(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        intermediate = {
            "anomalies": [
                {
                    "anomaly_type": "MEMORY_USAGE_INCREASE",
                    "anomaly_description": "frontend observed memory increased in the selected public window",
                    "entity_type": "Service",
                    "entity_id": "frontend",
                    "start_time": "2026-10-17T07:20:00Z",
                    "end_time": "2026-10-17T07:25:00Z",
                    "evidence": [{"modality": "metrics", "request_id": "req-1", "record_sha256": digest, "record": record}],
                }
            ]
        }
        task = {"task_id": "FT-01", "case_id": "SF-006", "kind": "FT"}
        with tempfile.TemporaryDirectory(prefix="final-test-") as tmp:
            run_dir = Path(tmp)
            answer = build_final_answer(intermediate, task=task, run_dir=run_dir, project_root=root)
            self.assertEqual(answer["schema_version"], "final-1.0")
            self.assertEqual(answer["task_id"], "FT-01")
            self.assertEqual(len(answer["charts"]), 1)
            artifact = answer["charts"][0]["artifact"]
            chart = run_dir / artifact["filename"]
            self.assertTrue(chart.is_file())
            self.assertEqual(hashlib.sha256(chart.read_bytes()).hexdigest(), artifact["sha256"])
            self.assertIsNone(answer["provider_reported_usage"])


if __name__ == "__main__":
    unittest.main()
