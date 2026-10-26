#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.security import derive_hash


def read_db(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"version": 1, "tokens": []}


def atomic_write(path: Path, payload: dict, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def create_record(team_id: str, *, mock_only: bool) -> tuple[dict, str]:
    token_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    salt = secrets.token_bytes(16).hex()
    prefix = "mock" if mock_only else "team"
    token = f"{prefix}.{token_id}.{secret}"
    record = {
        "token_id": token_id,
        "team_id": team_id,
        "salt": salt,
        "hash": derive_hash(secret, salt),
        "enabled": True,
        "mock_only": mock_only,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return record, token


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("/etc/aiops-contest/tokens.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-mock")
    init.add_argument("--count", type=int, default=17)
    init.add_argument("--client-file", type=Path, required=True)
    create = sub.add_parser("create")
    create.add_argument("--team-id", required=True)
    create.add_argument("--mock-only", action="store_true")
    create.add_argument("--output", type=Path, help="write the one-time plaintext token to this root-only file")
    disable = sub.add_parser("disable")
    disable.add_argument("--token-id", required=True)
    args = parser.parse_args()

    database = read_db(args.db)
    if args.command == "init-mock":
        if database["tokens"] or args.client_file.exists():
            print(json.dumps({"status": "PRESERVED_EXISTING", "count": len(database["tokens"])}))
            return
        if not 1 <= args.count <= 100:
            raise SystemExit("count must be between 1 and 100")
        clients = []
        for number in range(1, args.count + 1):
            team_id = f"MOCK-TEAM-{number:02d}"
            record, token = create_record(team_id, mock_only=True)
            database["tokens"].append(record)
            clients.append({"team_id": team_id, "token": token})
        atomic_write(args.db, database, 0o640)
        atomic_write(args.client_file, {"classification": "MOCK_ONLY", "clients": clients}, 0o600)
        print(json.dumps({"status": "CREATED_MOCK_ONLY", "count": len(clients)}))
        return

    if args.command == "create":
        if any(item["team_id"] == args.team_id and item["enabled"] for item in database["tokens"]):
            raise SystemExit("team already has an enabled token; disable or rotate it first")
        record, token = create_record(args.team_id, mock_only=args.mock_only)
        database["tokens"].append(record)
        atomic_write(args.db, database, 0o640)
        if args.output:
            atomic_write(args.output, {"team_id": args.team_id, "token": token}, 0o600)
            print(json.dumps({"status": "CREATED", "token_id": record["token_id"], "output": str(args.output)}))
        else:
            print(json.dumps({"status": "CREATED_HASH_ONLY", "token_id": record["token_id"]}))
        return

    found = False
    for record in database["tokens"]:
        if record["token_id"] == args.token_id:
            record["enabled"] = False
            record["disabled_at"] = datetime.now(timezone.utc).isoformat()
            found = True
    if not found:
        raise SystemExit("token_id not found")
    atomic_write(args.db, database, 0o640)
    print(json.dumps({"status": "DISABLED", "token_id": args.token_id}))


if __name__ == "__main__":
    main()

