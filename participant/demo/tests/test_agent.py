from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.core import AgentOptions, CompetitionAgent


class AgentFlowTests(unittest.TestCase):
    def test_main_state_machine_writes_schema_valid_answer(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            options = AgentOptions(
                case_id="SF-001", base_url="http://127.0.0.1:9", question="test",
                run_dir=Path(directory), project_root=root, config_path=root / "config.yaml",
            )
            agent = CompetitionAgent(options)
            with patch.object(agent, "collect", return_value=({}, [], [], [])), \
                 patch.object(agent, "analyze", return_value=[]), \
                 patch.object(agent, "decide", return_value=[]):
                answer = agent.run()
            self.assertEqual({
                "case_id": "SF-001", "schema_version": "3.0",
                "task_type": "SEMIFINAL_ANOMALY_DETECTION", "anomalies": [],
            }, answer)
            self.assertTrue((Path(directory) / "answer.json").exists())


if __name__ == "__main__":
    unittest.main()
