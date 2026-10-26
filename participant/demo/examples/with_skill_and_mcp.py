"""Run: python examples/with_skill_and_mcp.py"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
raise SystemExit(subprocess.call([
    sys.executable, str(ROOT / "main.py"), "--base-url", "http://192.168.51.51:8080",
    "--run-dir", str(ROOT / "runs/example-full"), "--skills-dir", str(ROOT / "skills"),
    "--mcp-config", str(ROOT / "configs/mcp_servers.json"), "--team-id", "example-full",
]))

