#!/usr/bin/env python3
"""Lock post-capture aggregation before the continuous window yields results."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path("/root/stage5-v3-collector-reliability")
OUTPUT = ROOT / "design/analysis-code-lock.json"
FILES = ("scripts/summarize_continuous.py", "scripts/qualify_network_controls.py",
         "scripts/archive_continuous_orchestrator.py", "scripts/build_pipeline_topology.py",
         "design/continuous-window-prereg.yaml", "design/network-control-prereg.yaml")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUTPUT.exists():
        raise ValueError("Refusing overwrite")
    sources = {str(ROOT / relative): sha(ROOT / relative) for relative in FILES}
    value = {"status": "POST_CAPTURE_ANALYSIS_LOCKED_BEFORE_RESULTS",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": sources,
        "collector_config_modified_by_analysis": False,
        "thresholds_changed_after_window_start": False,
        "formal_protocol_changed": False}
    OUTPUT.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "files": len(sources),
                      "sha256": sha(OUTPUT)}))


if __name__ == "__main__":
    main()
