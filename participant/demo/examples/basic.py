"""Run: python examples/basic.py"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
raise SystemExit(subprocess.call([
    sys.executable, str(ROOT / "main.py"), "--base-url", "http://192.168.51.51:8080",
    "--run-dir", str(ROOT / "runs/example-basic"), "--team-id", "example-basic",
]))

