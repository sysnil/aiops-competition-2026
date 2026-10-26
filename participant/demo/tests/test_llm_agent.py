from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.llm_client import AgentLLMConfig, LLMUnavailable
from agent.llm_core import LLMCompetitionAgent


class LLMAgentTests(unittest.TestCase):
    def test_missing_private_configuration_is_explicit(self) -> None:
        keys = {
            "AIOPS_AGENT_LLM_BASE_URL": "",
            "AIOPS_AGENT_LLM_MODEL": "",
            "AIOPS_AGENT_LLM_API_KEY": "",
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, keys, clear=False):
            with self.assertRaisesRegex(LLMUnavailable, "LLM_UNAVAILABLE"):
                AgentLLMConfig.load(Path(directory) / "missing.env", {})

    def test_env_file_is_independent_and_environment_can_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.env"
            path.write_text(
                "AIOPS_AGENT_LLM_BASE_URL=https://participant.example/v1\n"
                "AIOPS_AGENT_LLM_MODEL=participant-model\n"
                "AIOPS_AGENT_LLM_API_KEY=file-secret\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {
                "AIOPS_AGENT_LLM_BASE_URL": "https://participant.example/v1",
                "AIOPS_AGENT_LLM_MODEL": "override-model",
                "AIOPS_AGENT_LLM_API_KEY": "[REDACTED_CONFIGURE_LOCALLY]",
            }, clear=False):
                config = AgentLLMConfig.load(path, {})
            self.assertEqual("https://participant.example/v1", config.base_url)
            self.assertEqual("override-model", config.model)
            self.assertEqual("file-secret", config.api_key)

    def test_topology_summary_excludes_sensitive_verbose_identity_fields(self) -> None:
        payload = {
            "case_id": "SF-001",
            "topology": {
                "kind": "public",
                "snapshots": [{
                    "observed_at_utc": "2026-10-14T09:00:00Z",
                    "pods": [{
                        "name": "frontend-abc",
                        "uid": "must-not-be-copied",
                        "pod_ip": "10.0.0.1",
                        "service": "frontend",
                        "node": "worker-1",
                        "containers": [{"restart_count": 2}, {"restart_count": 1}],
                    }],
                }],
                "observed_service_edges": [{"source": "frontend", "destination": "cart"}],
            },
        }
        summary = LLMCompetitionAgent._topology_summary(payload)
        self.assertEqual(3, summary["latest_pods"][0]["restart_count"])
        self.assertNotIn("uid", summary["latest_pods"][0])
        self.assertNotIn("pod_ip", summary["latest_pods"][0])


if __name__ == "__main__":
    unittest.main()
