#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.final_store import FinalStore


BATCH_ID = "FINAL-STAGE2-AUTO-GT-20261019"
DEFAULT_CATALOG = Path("/etc/aiops-final-stage2/catalog.json")
DEFAULT_DATABASE = Path("/var/lib/aiops-final-stage2/stage2.sqlite3")


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def checked_store(catalog: Path, database: Path) -> FinalStore:
    payload = json.loads(catalog.read_text(encoding="utf-8"))
    if payload.get("batch_id") != BATCH_ID or not payload.get("preview_scoring", {}).get("internal_preview_only"):
        raise SystemExit("refusing to manage a non-Stage-2 catalog")
    if database.resolve() != DEFAULT_DATABASE.resolve():
        raise SystemExit("refusing to manage a database outside the isolated Stage-2 path")
    return FinalStore(database, catalog)


def main() -> None:
    parser = argparse.ArgumentParser(description="Control the isolated Final Stage 2 internal-preview batch")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    commands = parser.add_subparsers(dest="command", required=True)

    task = commands.add_parser("task")
    task.add_argument("task_id", choices=["FT-01", "FT-02", "FT-03"])
    task.add_argument("target", choices=["OPEN", "SUBMISSION_LOCKED", "SCORED", "CLOSED"])
    task.add_argument("--duration", type=int)

    configure = commands.add_parser("round-configure")
    configure.add_argument("round_id", choices=[f"PK-R{i:02d}" for i in range(1, 9)])
    configure.add_argument("champion")
    configure.add_argument("challenger")

    transition = commands.add_parser("round-transition")
    transition.add_argument("round_id", choices=[f"PK-R{i:02d}" for i in range(1, 9)])
    transition.add_argument("target", choices=["RUNNING", "SUBMISSION_LOCKED", "JUDGING", "CLOSED"])
    transition.add_argument("--duration", type=int)

    judge = commands.add_parser("judge")
    judge.add_argument("round_id", choices=[f"PK-R{i:02d}" for i in range(1, 9)])
    judge.add_argument("team_id")
    judge.add_argument("judge_id")
    judge.add_argument("score", type=float)
    judge.add_argument("--notes", default="Final Stage 2 simulated judge input")

    confirm = commands.add_parser("confirm")
    confirm.add_argument("round_id", choices=[f"PK-R{i:02d}" for i in range(1, 9)])

    summary = commands.add_parser("summary")
    summary.add_argument("--team-id")

    audit = commands.add_parser("audit")
    audit.add_argument("--limit", type=int, default=200)

    args = parser.parse_args()
    store = checked_store(args.catalog, args.database)
    actor = "FINAL-STAGE2-OPERATOR"

    if args.command == "task":
        emit(store.transition_task(args.task_id, args.target, actor, args.duration))
    elif args.command == "round-configure":
        emit(store.configure_round(args.round_id, args.champion, args.challenger, actor))
    elif args.command == "round-transition":
        emit(store.transition_round(args.round_id, args.target, actor, args.duration))
    elif args.command == "judge":
        store.judge(args.round_id, args.team_id, args.judge_id, args.score, args.notes, actor)
        emit({"status": "RECORDED", "round_id": args.round_id, "team_id": args.team_id, "judge_id": args.judge_id})
    elif args.command == "confirm":
        emit(store.confirm_round(args.round_id, actor))
    elif args.command == "audit":
        emit(store.audit(max(1, min(args.limit, 1000))))
    else:
        if args.team_id:
            emit(store.qualification_summary(args.team_id))
            return
        with sqlite3.connect(args.database) as db:
            db.row_factory = sqlite3.Row
            counts = {
                table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("final_tasks", "final_rounds", "final_submissions", "final_judges", "final_audit")
            }
        emit({"batch_id": BATCH_ID, "tasks": store.tasks(), "rounds": store.rounds(), "counts": counts})


if __name__ == "__main__":
    main()
