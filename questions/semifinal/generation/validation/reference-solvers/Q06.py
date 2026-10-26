#!/usr/bin/env python3
"""Public-first solver for one registered semifinal question."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from solver_runtime import run
run("Q06")
