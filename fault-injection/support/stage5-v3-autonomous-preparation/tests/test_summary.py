"""Missing live evidence remains NOT_RUN/BLOCKED in the final summary."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_autonomous_summary.py"


class SummaryTests(unittest.TestCase):
    def test_absent_clean_and_fault_results_cannot_be_called_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "results").mkdir()
            checks = root / "checks.json"
            checks.write_text('{"status":"PASS"}\n')
            demo = root / "demo.json"
            demo.write_text(json.dumps({"attempts": 56,
                "split_states": {"DEV": "SCORES_FROZEN", "TEST": "SCORES_FROZEN"},
                "real_faults": 0, "real_gt_reads": 0, "formal_predictions": 0,
                "events": 394, "event_chain_head_sha256": "synthetic"}) + "\n")
            output = root / "results/autonomous-preparation-summary.json"
            subprocess.run([sys.executable, str(SCRIPT), "--root", str(root),
                "--offline-checks", str(checks), "--synthetic-demo", str(demo),
                "--output", str(output)], check=True, capture_output=True)
            value = json.loads(output.read_text())
            self.assertEqual(value["phase_status"]["C"], "BLOCKED")
            self.assertEqual(value["phase_status"]["D"], "NOT_RUN")
            self.assertEqual(value["phase_status"]["E"], "NOT_RUN")
            self.assertEqual(value["faults_injected"], 0)
            self.assertFalse(value["recommend_stage5_v3_protocol_freeze_review"])


if __name__ == "__main__":
    unittest.main()
