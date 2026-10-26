#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.security import derive_hash


TEAMS = [
    ("TEAM-01", "京区协作一片", "192.168.51.52"),
    ("TEAM-02", "京区协作二片", "192.168.51.53"),
    ("TEAM-03", "京区协作三片", "192.168.51.54"),
    ("TEAM-04", "京区协作四片", "192.168.51.55"),
    ("TEAM-05", "沈阳分院", "192.168.51.56"),
    ("TEAM-06", "上海分院1队", "192.168.51.57"),
    ("TEAM-07", "上海分院2队", "192.168.51.58"),
    ("TEAM-08", "武汉分院", "192.168.51.59"),
    ("TEAM-09", "广州分院", "192.168.51.60"),
    ("TEAM-10", "成都分院", "192.168.51.61"),
    ("TEAM-11", "昆明分院", "192.168.51.62"),
    ("TEAM-12", "西安分院", "192.168.51.63"),
    ("TEAM-13", "兰州分院", "192.168.51.64"),
    ("TEAM-14", "合肥物质科学研究院", "192.168.51.65"),
    ("TEAM-15", "国科大", "192.168.51.66"),
    ("TEAM-16", "中科大", "192.168.51.67"),
    ("TEAM-17", "京区企业", "192.168.51.68"),
]


def token_record(team_id: str, team_name: str, assigned_ip: str | None, organizer: bool = False):
    token_id = ("ORGTEST" if organizer else team_id.replace("-", "")) + "-" + secrets.token_hex(4).upper()
    secret = secrets.token_urlsafe(36)
    prefix = "organizer" if organizer else "team"
    token = f"{prefix}.{token_id}.{secret}"
    salt = os.urandom(16).hex()
    now = datetime.now(timezone.utc).isoformat()
    return ({
        "team_id": team_id, "team_name": team_name, "assigned_ip": assigned_ip,
        "session": "UNASSIGNED", "token_id": token_id, "token_status": "ACTIVE",
        "allowed_modes": ["PRACTICE", "SEMIFINAL"], "created_at": now,
        "token_salt": salt, "token_hash": derive_hash(secret, salt),
        "is_organizer_test": organizer,
    }, {"team_id": team_id, "team_name": team_name, "assigned_ip": assigned_ip, "token_id": token_id, "token": token})


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the initial 17-team hashed registry and one-time credential bundle")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--legacy-token-file", type=Path)
    args = parser.parse_args()
    if args.registry.exists() or args.credentials.exists():
        raise SystemExit("refusing to overwrite an existing registry or credential bundle")
    now = datetime.now(timezone.utc).isoformat()
    records, credentials = [], []
    for values in TEAMS:
        record, credential = token_record(*values)
        records.append(record); credentials.append(credential)
    record, credential = token_record("ORGANIZER-TEST", "组委会 Agent 测试身份", "192.168.51.50", True)
    records.append(record); credentials.append(credential)
    if args.legacy_token_file and args.legacy_token_file.is_file():
        legacy = json.loads(args.legacy_token_file.read_text(encoding="utf-8"))
        for item in legacy.get("tokens", []):
            if not item.get("enabled", False):
                continue
            records.append({
                "team_id": item["team_id"], "team_name": "历史兼容身份", "assigned_ip": None,
                "session": "UNASSIGNED", "token_id": item["token_id"], "token_status": "ACTIVE",
                "allowed_modes": ["PRACTICE"], "created_at": now,
                "token_salt": item["salt"], "token_hash": item["hash"],
                "mock_only": bool(item.get("mock_only", False)), "legacy": True,
            })
    args.registry.write_text(json.dumps({"version": 1, "created_at": now, "teams": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.credentials.write_text(json.dumps({"created_at": now, "credentials": credentials}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.registry.chmod(0o640); args.credentials.chmod(0o600)
    print(json.dumps({"teams": 17, "organizer_test": 1, "registry": str(args.registry), "credentials": str(args.credentials)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
