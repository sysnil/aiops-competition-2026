#!/usr/bin/env python3
"""Prepare a validated CONTEST_EXCHANGE_V2 package for the existing importer."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import duckdb

from case_utils import manifest_hash, sha256_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--version", default="public-v1")
    args = parser.parse_args()
    source = args.source.resolve()
    destination = args.destination.resolve()
    validation = json.loads(args.validation.read_text(encoding="utf-8"))
    if validation.get("status") != "PASS":
        raise SystemExit("exchange validation did not pass")
    source_manifest_path = source / "manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_manifest_sha = sha256_file(source_manifest_path)
    case_id = str(source_manifest.get("case_id", ""))
    if (source_manifest_sha != validation.get("manifest_sha256") or
            case_id != validation.get("manifest", {}).get("case_id")):
        raise SystemExit("validation result does not match current source manifest")
    if destination.exists():
        raise SystemExit("compatibility staging destination already exists")
    destination.mkdir(parents=True, mode=0o700)

    copied = ("metrics.parquet", "logs.parquet", "traces.parquet")
    for name in copied:
        shutil.copy2(source / name, destination / name)
    shutil.copy2(source_manifest_path, destination / "source-manifest.json")
    shutil.copy2(source / "topology.json", destination / "source-topology.json")
    shutil.copy2(source / "trace_index.parquet", destination / "source-trace_index.parquet")

    connection = duckdb.connect(":memory:")
    paths = [str(source / name) for name in copied]
    services = connection.execute(
        "SELECT DISTINCT service FROM ("
        "SELECT service FROM read_parquet(?) UNION ALL "
        "SELECT service FROM read_parquet(?) UNION ALL "
        "SELECT service FROM read_parquet(?)) "
        "WHERE service IS NOT NULL AND service <> '' ORDER BY service",
        paths,
    ).fetchnumpy()["service"].tolist()
    sample_trace = connection.execute(
        "SELECT trace_id FROM read_parquet(?) "
        "GROUP BY trace_id HAVING count(DISTINCT service) >= 2 "
        "AND count(*) FILTER (WHERE service='frontend') > 0 "
        "AND count(*) FILTER (WHERE service='checkout') > 0 "
        "ORDER BY min(start_time), trace_id LIMIT 1",
        [str(source / "traces.parquet")],
    ).fetchone()
    topology = json.loads((source / "topology.json").read_text(encoding="utf-8"))
    relations = validation["relationships"]
    row_counts = validation["row_counts"]["actual"]

    files = []
    for name, key in (
        ("metrics.parquet", "metrics"),
        ("logs.parquet", "logs"),
        ("traces.parquet", "traces"),
    ):
        path = destination / name
        files.append({
            "name": name,
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
            "row_count": row_counts[key],
        })

    manifest = {
        "case_id": case_id,
        "version": args.version,
        "dataset_schema_version": source_manifest["schema_version"],
        "start_time": source_manifest["window"]["start_utc"],
        "end_time": source_manifest["window"]["end_utc"],
        "available_modalities": ["metrics", "logs", "traces"],
        "public_services": services,
        "public_topology": topology,
        "data_source": f"{case_id}_PUBLIC_CONTEST_EXCHANGE_V2",
        "classification": "PUBLIC_COMPETITION_DATA",
        "publication_status": "staging",
        "files": files,
        "sample_trace_id": sample_trace[0] if sample_trace else None,
        "source_manifest_sha256": source_manifest_sha,
        "source_original_files": source_manifest["files"],
        "source_row_counts": source_manifest["row_counts"],
        "source_unique_trace_count": source_manifest["unique_trace_count"],
        "source_exchange_conditions": relations,
        "allowed_source_conditions": {
            "duplicate_metric_identity_groups": relations["duplicate_metric_identities"],
            "duplicate_metric_full_row_groups": 0,
            "unresolved_parent_spans": relations["unresolved_parent_spans"],
            "unresolved_log_trace_rows": relations["log_trace_ids_missing_from_traces"],
        },
        "compatibility_conversion": {
            "input_schema": "CONTEST_EXCHANGE_V2",
            "output_schema": "AIOPS_CONTEST_SERVER_MANIFEST_V1",
            "parquet_record_transformation": "NONE_IN_COMPATIBILITY_STAGE",
            "rules": [
                "Preserve every source Parquet row and value byte-for-byte in compatibility staging.",
                "Map source window to start_time/end_time.",
                "Embed the supplied public topology JSON in public_topology.",
                "Declare observed source integrity conditions so the existing validator retains rather than deletes them.",
                "The existing importer subsequently performs stable sorting and Zstandard re-encoding only.",
            ],
            "before_rows": {
                "metrics": row_counts["metrics"],
                "logs": row_counts["logs"],
                "traces": row_counts["traces"],
                "trace_index": row_counts["trace_index"],
            },
            "compatibility_stage_rows": {
                "metrics": row_counts["metrics"],
                "logs": row_counts["logs"],
                "traces": row_counts["traces"],
            },
        },
    }
    manifest["immutable_manifest_hash"] = manifest_hash(manifest)
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    conversion_record = {
        "status": "PREPARED",
        "source": str(source),
        "destination": str(destination),
        "source_manifest_sha256": source_manifest_sha,
        "source_validation": str(args.validation.resolve()),
        "files": {
            path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in sorted(destination.iterdir()) if path.is_file()
        },
        "manifest": manifest,
    }
    (destination / "conversion-record.json").write_text(
        json.dumps(conversion_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": "PREPARED", "destination": str(destination),
        "source_manifest_sha256": source_manifest_sha,
        "rows": row_counts, "services": len(services),
        "sample_trace_id": manifest["sample_trace_id"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
