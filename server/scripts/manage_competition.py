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


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, payload: dict, mode: int = 0o640) -> None:
    owner = path.stat() if path.exists() else None
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(mode)
    if owner is not None:
        os.chown(temporary, owner.st_uid, owner.st_gid)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage team sessions, mode state, publication and token lifecycle")
    parser.add_argument("--registry", type=Path, default=Path("/etc/aiops-contest/team_registry.json"))
    parser.add_argument("--state-file", type=Path, default=Path("/etc/aiops-contest/competition_state.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    session = sub.add_parser("assign-session"); session.add_argument("team_id"); session.add_argument("session", choices=["AM", "PM", "UNASSIGNED"])
    state = sub.add_parser("set-state"); state.add_argument("mode", choices=["practice", "semifinal"]); state.add_argument("state"); state.add_argument("--session", choices=["AM", "PM"])
    publication = sub.add_parser("set-publication"); publication.add_argument("mode", choices=["practice", "semifinal"]); publication.add_argument("case_id"); publication.add_argument("action", choices=["publish", "unpublish"])
    token = sub.add_parser("token"); token.add_argument("team_id"); token.add_argument("action", choices=["activate", "revoke", "rotate"]); token.add_argument("--credential-output", type=Path)
    sub.add_parser("status")
    args = parser.parse_args()
    if args.command == "status":
        registry, state_data = load(args.registry), load(args.state_file)
        print(json.dumps({"team_count": sum(1 for x in registry["teams"] if x["team_id"].startswith("TEAM-")), "sessions": {name: sum(1 for x in registry["teams"] if x.get("session") == name) for name in ("AM", "PM", "UNASSIGNED")}, "practice": state_data["practice"], "semifinal": state_data["semifinal"]}, ensure_ascii=False, indent=2)); return 0
    if args.command == "assign-session":
        payload = load(args.registry); record = next((x for x in payload["teams"] if x["team_id"] == args.team_id), None)
        if not record: raise SystemExit("unknown team_id")
        record["session"] = args.session; save(args.registry, payload); return 0
    if args.command == "set-state":
        payload = load(args.state_file)
        if args.mode == "practice":
            if args.session: raise SystemExit("practice has no AM/PM session")
            if args.state not in {"OPEN", "CLOSED", "EMERGENCY_LOCKED"}: raise SystemExit("invalid practice state")
            payload["practice"]["state"] = args.state
        else:
            if not args.session: raise SystemExit("--session is required for semifinal")
            if args.state not in {"NOT_OPEN", "OPEN", "CLOSED", "EMERGENCY_LOCKED"}: raise SystemExit("invalid semifinal state")
            payload["semifinal"]["sessions"][args.session]["state"] = args.state
        save(args.state_file, payload); return 0
    if args.command == "set-publication":
        payload = load(args.state_file); values = set(payload[args.mode]["published_cases"])
        if args.action == "publish": values.add(args.case_id)
        else: values.discard(args.case_id)
        payload[args.mode]["published_cases"] = sorted(values); save(args.state_file, payload); return 0
    payload = load(args.registry); record = next((x for x in payload["teams"] if x["team_id"] == args.team_id), None)
    if not record: raise SystemExit("unknown team_id")
    if args.action != "rotate":
        record["token_status"] = "ACTIVE" if args.action == "activate" else "REVOKED"; save(args.registry, payload); return 0
    if not args.credential_output: raise SystemExit("--credential-output is required for rotation")
    secret, salt = secrets.token_urlsafe(36), os.urandom(16).hex()
    record["token_id"] = record["team_id"].replace("-", "") + "-" + secrets.token_hex(4).upper()
    record["token_salt"], record["token_hash"], record["token_status"] = salt, derive_hash(secret, salt), "ACTIVE"
    prefix = "organizer" if record.get("is_organizer_test") else "team"
    args.credential_output.write_text(json.dumps({"team_id": record["team_id"], "token_id": record["token_id"], "token": f"{prefix}.{record['token_id']}.{secret}"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.credential_output.chmod(0o600); save(args.registry, payload); return 0


if __name__ == "__main__":
    raise SystemExit(main())
