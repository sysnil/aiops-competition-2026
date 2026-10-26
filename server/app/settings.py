from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Settings:
    public_root: Path
    mock_root: Path
    staging_root: Path
    index_root: Path
    cache_root: Path
    token_db: Path
    team_registry: Path
    competition_state: Path
    requests_per_minute: int
    per_team_concurrency: int
    total_query_concurrency: int
    max_rows: int
    max_page: int
    max_time_range_seconds: int
    query_timeout_seconds: float
    max_trace_spans: int
    duckdb_threads: int
    duckdb_memory_limit: str
    duckdb_temp_directory: Path


def _positive(config: dict[str, Any], key: str, *, maximum: int | None = None) -> int:
    value = int(config[key])
    if value <= 0 or (maximum is not None and value > maximum):
        raise ValueError(f"invalid positive setting: {key}")
    return value


def load_settings(path: str | Path | None = None) -> Settings:
    default = Path(__file__).resolve().parents[1] / "config" / "config.yaml"
    config_path = Path(path or os.getenv("AIOPS_CONFIG", default)).resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    data = raw["data"]
    limits = raw["limits"]
    duck = raw["duckdb"]
    return Settings(
        public_root=Path(data["public_root"]).resolve(),
        mock_root=Path(data["mock_root"]).resolve(),
        staging_root=Path(data["staging_root"]).resolve(),
        index_root=Path(data["index_root"]).resolve(),
        cache_root=Path(data["cache_root"]).resolve(),
        token_db=Path(raw["auth"]["token_db"]).resolve(),
        team_registry=Path(raw["auth"].get("team_registry", "/etc/aiops-contest/team_registry.json")).resolve(),
        competition_state=Path(raw["auth"].get("competition_state", "/etc/aiops-contest/competition_state.json")).resolve(),
        requests_per_minute=_positive(limits, "requests_per_minute", maximum=10000),
        per_team_concurrency=_positive(limits, "per_team_concurrency", maximum=64),
        total_query_concurrency=_positive(limits, "total_query_concurrency", maximum=128),
        max_rows=_positive(limits, "max_rows", maximum=10000),
        max_page=_positive(limits, "max_page", maximum=100000),
        max_time_range_seconds=_positive(limits, "max_time_range_seconds", maximum=3600),
        query_timeout_seconds=float(limits["query_timeout_seconds"]),
        max_trace_spans=_positive(limits, "max_trace_spans", maximum=50000),
        duckdb_threads=_positive(duck, "threads", maximum=4),
        duckdb_memory_limit=str(duck["memory_limit"]),
        duckdb_temp_directory=Path(duck["temp_directory"]).resolve(),
    )
