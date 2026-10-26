from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline organizer control for final PK rounds")
    parser.add_argument("action", choices=["show", "configure", "open", "close"])
    parser.add_argument("--state", type=Path, default=Path("/etc/aiops-contest/final_rounds.json"))
    parser.add_argument("--round", dest="round_id", default="TEST-R01")
    parser.add_argument("--champion")
    parser.add_argument("--challenger")
    parser.add_argument("--case")
    parser.add_argument("--duration-seconds", type=int)
    args = parser.parse_args()
    payload = json.loads(args.state.read_text(encoding="utf-8"))
    item = next((value for value in payload.get("rounds", []) if value.get("round_id") == args.round_id), None)
    if not item:
        raise SystemExit("round not found")
    if args.action == "show":
        print(json.dumps(item, ensure_ascii=False, indent=2))
        return
    if args.action == "configure":
        if item.get("status") == "OPEN":
            raise SystemExit("cannot reconfigure an open round")
        if not all((args.champion, args.challenger, args.case)):
            raise SystemExit("configure requires --champion, --challenger and --case")
        if args.champion == args.challenger:
            raise SystemExit("final round requires two different teams")
        item.update({"champion_team_id": args.champion, "challenger_team_id": args.challenger, "case_id": args.case, "status": "CONFIGURED", "opens_at": None, "closes_at": None})
    elif args.action == "open":
        if item.get("status") != "CONFIGURED" or not item.get("case_id"):
            raise SystemExit("round must be configured before opening")
        duration = args.duration_seconds or item.get("time_limit_seconds")
        if not duration or not 60 <= int(duration) <= 14400:
            raise SystemExit("duration must be between 60 and 14400 seconds")
        now = datetime.now(timezone.utc)
        item.update({"status": "OPEN", "opens_at": now.isoformat(), "closes_at": (now + timedelta(seconds=int(duration))).isoformat(), "time_limit_seconds": int(duration)})
    else:
        if item.get("status") != "OPEN":
            raise SystemExit("only an open round can be closed")
        item.update({"status": "CLOSED", "closes_at": datetime.now(timezone.utc).isoformat()})
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write(args.state, payload)
    audit = args.state.with_suffix(".audit.jsonl")
    with audit.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"timestamp": payload["updated_at"], "action": args.action, "round_id": args.round_id, "status": item["status"]}, ensure_ascii=False) + "\n")
    os.chmod(audit, 0o640)
    print(json.dumps({"round_id": args.round_id, "status": item["status"], "updated_at": payload["updated_at"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
