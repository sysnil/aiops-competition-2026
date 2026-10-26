#!/usr/bin/env python3
"""Validate and atomically publish a standardized public competition data package."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import uuid
from pathlib import Path

import pandas as pd

from case_utils import CASE_ID_RE, manifest_hash, sha256_file, validate_case_directory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path, help="directory containing manifest.json and three Parquet files")
    parser.add_argument("--public-root", type=Path, default=Path("/data/aiops-contest/public"))
    parser.add_argument("--staging-root", type=Path, default=Path("/data/aiops-contest/staging"))
    parser.add_argument("--validate-only", action="store_true", help="normalize and validate in staging without publishing")
    args = parser.parse_args()
    source = args.package.resolve()
    source_manifest = source / "manifest.json"
    if not source_manifest.is_file():
        raise SystemExit("manifest.json is required")
    manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    case_id = str(manifest.get("case_id", ""))
    version = str(manifest.get("version", ""))
    if not CASE_ID_RE.fullmatch(case_id) or not CASE_ID_RE.fullmatch(version.upper()):
        raise SystemExit("invalid case_id or version")
    if manifest.get("classification") not in {"PUBLIC_COMPETITION_DATA", "PUBLIC_SAMPLE"}:
        raise SystemExit("input classification is not approved public data")
    if manifest.get("immutable_manifest_hash") != manifest_hash(manifest):
        raise SystemExit("input immutable manifest hash mismatch")

    args.staging_root.mkdir(parents=True, exist_ok=True)
    stage = args.staging_root / f"import-{case_id}-{version}-{uuid.uuid4().hex}"
    stage.mkdir(mode=0o700)
    try:
        for name in ("metrics.parquet", "logs.parquet", "traces.parquet"):
            shutil.copy2(source / name, stage / name)
        input_result = validate_case_directory(stage, manifest)
        if input_result["status"] != "PASS":
            (stage / "validation-error.json").write_text(
                json.dumps(input_result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
            )
            raise SystemExit("case validation failed; staging was preserved")

        metrics = pd.read_parquet(stage / "metrics.parquet").sort_values(
            ["timestamp", "metric_name", "service", "pod", "node"]
        )
        logs = pd.read_parquet(stage / "logs.parquet").sort_values(
            ["timestamp", "service", "pod", "severity", "trace_id", "span_id"]
        )
        traces = pd.read_parquet(stage / "traces.parquet").sort_values(
            ["trace_id", "start_time", "span_id"]
        )
        metrics.to_parquet(stage / "metrics.parquet", index=False, compression="zstd")
        logs.to_parquet(stage / "logs.parquet", index=False, compression="zstd")
        traces.to_parquet(stage / "traces.parquet", index=False, compression="zstd", row_group_size=4096)
        trace_index = traces.groupby("trace_id", as_index=False).agg(
            start_time=("start_time", "min"), span_count=("span_id", "count")
        ).sort_values("trace_id")
        trace_index.to_parquet(stage / "trace_index.parquet", index=False, compression="zstd")
        row_counts = {
            "metrics.parquet": len(metrics), "logs.parquet": len(logs),
            "traces.parquet": len(traces), "trace_index.parquet": len(trace_index),
        }
        manifest["files"] = []
        for name, row_count in row_counts.items():
            path = stage / name
            manifest["files"].append(
                {"name": name, "sha256": sha256_file(path), "size_bytes": path.stat().st_size,
                 "row_count": row_count}
            )
        manifest["publication_status"] = "staging"
        manifest["immutable_manifest_hash"] = manifest_hash(manifest)
        result = validate_case_directory(stage, manifest)
        if result["status"] != "PASS":
            (stage / "normalization-error.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
            )
            raise SystemExit("normalized case validation failed; staging was preserved")
        if args.validate_only:
            (stage / "normalized-manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(
                json.dumps(
                    {"status": "VALIDATED_ONLY", "case_id": case_id, "version": version,
                     "staging": str(stage), "validation": result},
                    ensure_ascii=False, indent=2, default=str,
                )
            )
            return
        destination = args.public_root / "cases" / case_id / version
        manifest_path = args.public_root / "manifests" / f"{case_id}.json"
        if destination.exists() or manifest_path.exists():
            raise SystemExit("immutable destination already exists; publish a new version instead")
        destination.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest["publication_status"] = "published"
        manifest["immutable_manifest_hash"] = manifest_hash(manifest)
        final_result = validate_case_directory(stage, manifest)
        if final_result["status"] != "PASS":
            raise SystemExit("final manifest validation failed; staging was preserved")
        os.rename(stage, destination)
        temporary = manifest_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, manifest_path)
        print(json.dumps({"status": "PUBLISHED", "case_id": case_id, "version": version}, indent=2))
    except Exception:
        if stage.exists():
            (stage / "IMPORT_FAILED").touch(exist_ok=True)
        raise


if __name__ == "__main__":
    main()
