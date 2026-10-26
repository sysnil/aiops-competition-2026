from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.evidence import evidence_from_record, validate_evidence


class EvidenceTests(unittest.TestCase):
    def test_evidence_links_to_logged_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api_requests.jsonl").write_text(
                json.dumps({"request_id": "req-0001", "status": 200}) + "\n", encoding="utf-8"
            )
            record = {"timestamp": "2026-10-14T09:00:00Z", "service": "svc", "body": "error", "_request_id": "req-0001"}
            anomaly = {"evidence": [evidence_from_record("logs", record)]}
            result = validate_evidence([anomaly], root)
            self.assertTrue(result["valid"])


if __name__ == "__main__":
    unittest.main()

