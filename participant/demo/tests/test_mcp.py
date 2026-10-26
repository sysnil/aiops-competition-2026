from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from agent.mcp_client import MCPToolClient


class MCPTests(unittest.TestCase):
    def test_server_is_discoverable_over_stdio(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            client = MCPToolClient(
                root / "configs/mcp_servers.json", root,
                "http://127.0.0.1:9", directory,
            )
            discovered = client.discover()
        self.assertTrue(discovered[0]["ok"], discovered)
        self.assertIn("analyze_edge_latency", {x["name"] for x in discovered[0]["tools"]})

    def test_failed_server_degrades_to_unknown(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "bad.json"
            config.write_text(json.dumps({"servers": [{"name": "bad", "command": "/missing/mcp", "enabled": True}]}))
            client = MCPToolClient(config, root, "http://127.0.0.1:9", directory)
            result = client.call("analyze_edge_latency", {})
        self.assertEqual("UNKNOWN", result["supported_conclusion"])


if __name__ == "__main__":
    unittest.main()
