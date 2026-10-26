#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.security import derive_hash


BATCH_ID = "FINAL-STAGE2-AUTO-GT-20261019"


def write_new(path: Path, payload: str, mode: int) -> None:
    if path.exists():
        raise SystemExit(f"refusing to overwrite: {path}")
    path.write_text(payload, encoding="utf-8")
    os.chmod(path, mode)


def identity(team_id: str, name: str, *, organizer: bool) -> tuple[dict, dict]:
    token_id = f"STAGE2-{secrets.token_hex(5).upper()}"
    secret = secrets.token_urlsafe(36)
    salt = os.urandom(16).hex()
    token = f"stage2.{token_id}.{secret}"
    record = {
        "team_id": team_id,
        "team_name": name,
        "assigned_ip": "192.168.51.50",
        "session": "FINAL-STAGE2",
        "token_id": token_id,
        "token_status": "ACTIVE",
        "allowed_modes": ["FINAL"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "token_salt": salt,
        "token_hash": derive_hash(secret, salt),
        "is_organizer_test": organizer,
    }
    return record, {"team_id": team_id, "token_id": token_id, "token": token}


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the isolated Final Stage 2 internal-preview configuration")
    parser.add_argument("--config-root", type=Path, default=Path("/etc/aiops-final-stage2"))
    parser.add_argument("--state-root", type=Path, default=Path("/var/lib/aiops-final-stage2"))
    args = parser.parse_args()
    config_root = args.config_root.resolve()
    state_root = args.state_root.resolve()
    if config_root.exists() or state_root.exists():
        raise SystemExit("Stage 2 roots already exist; refusing to overwrite the immutable batch")
    config_root.mkdir(parents=True, mode=0o750)
    state_root.mkdir(parents=True, mode=0o750)

    catalog = {
        "version": "final-stage2-auto-gt-v1",
        "publication_state": "INTERNAL_DEVELOPMENT_ONLY",
        "batch_id": BATCH_ID,
        "preview_scoring": {
            "version": "final-stage2-auto-gt-1.0",
            "base_max": 30,
            "pk_auto_max": 40,
            "judge_max": 30,
            "root_bonus_max": 0,
            "internal_preview_only": True,
        },
        "public_prompt": "请使用 Agent 对指定时间范围内的系统运行状况进行分析，自主识别异常现象及其影响实体，提供监控图表和可验证的数据证据，并提交结构化异常调查结果。",
        "tasks": [
            {"task_id": "FT-01", "case_id": "SF-006", "kind": "FT", "max_submissions": 1},
            {"task_id": "FT-02", "case_id": "SF-011", "kind": "FT", "max_submissions": 1},
            {"task_id": "FT-03", "case_id": "SF-007", "kind": "FT", "max_submissions": 1},
            {"task_id": "PK-R01", "case_id": "SF-004", "kind": "PK", "max_submissions": 1},
            {"task_id": "PK-R02", "case_id": "SF-008", "kind": "PK", "max_submissions": 1},
            {"task_id": "PK-R03", "case_id": "SF-009", "kind": "PK", "max_submissions": 1},
            {"task_id": "PK-R04", "case_id": "SF-012", "kind": "PK", "max_submissions": 1},
            {"task_id": "PK-R05", "case_id": "SF-004", "kind": "PK", "max_submissions": 1, "simulation_only": True},
            {"task_id": "PK-R06", "case_id": "SF-008", "kind": "PK", "max_submissions": 1, "simulation_only": True},
            {"task_id": "PK-R07", "case_id": "SF-009", "kind": "PK", "max_submissions": 1, "simulation_only": True},
            {"task_id": "PK-R08", "case_id": "SF-012", "kind": "PK", "max_submissions": 1, "simulation_only": True},
        ],
    }
    state = {
        "version": 1,
        "practice": {"state": "CLOSED", "published_cases": []},
        "semifinal": {"published_cases": [], "internal_preview_cases": [], "sessions": {"AM": {"state": "NOT_OPEN"}, "PM": {"state": "NOT_OPEN"}}},
        "final": {"state": "INTERNAL_PREVIEW", "batch_id": BATCH_ID},
    }
    records, credentials = [], []
    for values in (
        ("STAGE2-TEAM-A", "Final Stage 2 测试队 A", False),
        ("STAGE2-TEAM-B", "Final Stage 2 测试队 B", False),
        ("STAGE2-OPERATOR", "Final Stage 2 组委会只读联调", True),
    ):
        team_id, name, organizer = values
        record, credential = identity(team_id, name, organizer=organizer)
        records.append(record)
        credentials.append(credential)
    registry = {"version": 1, "batch_id": BATCH_ID, "teams": records}
    credential_bundle = {"classification": "FINAL_STAGE2_INTERNAL_ONLY", "batch_id": BATCH_ID, "credentials": credentials}

    config = {
        "data": {
            "public_root": "/data/aiops-contest/public",
            "mock_root": "/data/aiops-contest/mock-only",
            "staging_root": "/data/aiops-contest/staging",
            "index_root": "/data/aiops-contest/indexes",
            "cache_root": "/data/aiops-contest/cache",
        },
        "auth": {
            "token_db": str(config_root / "unused-tokens.json"),
            "team_registry": str(config_root / "team-registry.json"),
            "competition_state": str(config_root / "competition-state.json"),
        },
        "limits": {
            "requests_per_minute": 60,
            "per_team_concurrency": 4,
            "total_query_concurrency": 12,
            "max_rows": 1000,
            "max_page": 10000,
            "max_time_range_seconds": 3600,
            "query_timeout_seconds": 3.0,
            "max_trace_spans": 5000,
        },
        "duckdb": {
            "threads": 2,
            "memory_limit": "768MB",
            "temp_directory": "/data/aiops-contest/cache/duckdb",
        },
    }
    write_new(config_root / "catalog.json", json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", 0o640)
    write_new(config_root / "competition-state.json", json.dumps(state, ensure_ascii=False, indent=2) + "\n", 0o640)
    write_new(config_root / "team-registry.json", json.dumps(registry, ensure_ascii=False, indent=2) + "\n", 0o640)
    write_new(config_root / "credentials.json", json.dumps(credential_bundle, ensure_ascii=False, indent=2) + "\n", 0o600)
    write_new(config_root / "config.yaml", yaml.safe_dump(config, allow_unicode=True, sort_keys=False), 0o640)
    write_new(config_root / "unused-tokens.json", '{"version":1,"tokens":[]}\n', 0o640)
    write_new(
        config_root / "legacy-final-rounds.json",
        json.dumps({"version": 1, "state": "CLOSED", "rounds": []}, ensure_ascii=False, indent=2) + "\n",
        0o640,
    )
    print(json.dumps({"status": "CREATED", "batch_id": BATCH_ID, "identities": [x["team_id"] for x in credentials]}))


if __name__ == "__main__":
    main()
