#!/usr/bin/env python3
"""Exercise the formal import pipeline with a synthetic public sample, without publishing it."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from case_utils import manifest_hash


ROOT = Path("/opt/aiops-contest-server")
MOCK_ROOT = Path("/data/aiops-contest/mock-only")


def main() -> None:
    source_manifest = json.loads(
        (MOCK_ROOT / "manifests" / "MOCK-SF-001.json").read_text(encoding="utf-8")
    )
    source_dir = MOCK_ROOT / "cases" / "MOCK-SF-001" / source_manifest["version"]
    with tempfile.TemporaryDirectory(prefix="aiops-import-sample-") as temporary:
        package = Path(temporary)
        for name in ("metrics.parquet", "logs.parquet", "traces.parquet"):
            shutil.copy2(source_dir / name, package / name)
        source_manifest["case_id"] = "IMPORT-SAMPLE-001"
        source_manifest["version"] = "V001"
        source_manifest["classification"] = "PUBLIC_SAMPLE"
        source_manifest["publication_status"] = "package"
        source_manifest["data_source"] = "SYNTHETIC_IMPORT_PIPELINE_TEST"
        source_manifest["files"] = [
            item for item in source_manifest["files"] if item["name"] in {
                "metrics.parquet", "logs.parquet", "traces.parquet"
            }
        ]
        source_manifest["immutable_manifest_hash"] = manifest_hash(source_manifest)
        (package / "manifest.json").write_text(
            json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        completed = subprocess.run(
            [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/import_case.py"),
             str(package), "--validate-only"],
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"import validation failed rc={completed.returncode}\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        result = json.loads(completed.stdout)
        if result["status"] != "VALIDATED_ONLY" or result["validation"]["status"] != "PASS":
            raise RuntimeError(completed.stdout)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
