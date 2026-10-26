#!/usr/bin/env python3
"""Root-only, offline Final controller. Stage 1 mutations require simulation mode."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.final_store import FinalStore  # noqa: E402
from app.storage import StorageError  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=[
        "tasks", "rounds", "audit", "task-open", "task-lock", "task-score", "task-close",
        "round-assign-simulation-case", "round-configure", "round-start", "round-lock",
        "round-judging", "round-judge", "round-confirm", "round-close",
    ])
    parser.add_argument("--database", type=Path, default=Path("/var/lib/aiops-grading/submissions.sqlite3"))
    parser.add_argument("--catalog", type=Path, default=Path("/opt/aiops-contest-server/config/final-stage1-catalog.json"))
    parser.add_argument("--simulation", action="store_true", help="use an isolated test database; never opens the competition")
    parser.add_argument("--task")
    parser.add_argument("--round")
    parser.add_argument("--champion")
    parser.add_argument("--challenger")
    parser.add_argument("--case")
    parser.add_argument("--team")
    parser.add_argument("--judge")
    parser.add_argument("--score", type=float)
    parser.add_argument("--notes", default="")
    parser.add_argument("--duration-seconds", type=int)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("offline Final control requires root")
    production = Path("/var/lib/aiops-grading/submissions.sqlite3").resolve()
    if args.simulation and args.database.resolve() == production:
        raise SystemExit("simulation must use a separate database")
    readonly = args.action in {"tasks", "rounds", "audit"}
    if not readonly and not args.simulation:
        raise SystemExit("Stage 1 formal mutations are blocked until GT review, weights lock and organizer authorization")
    store = FinalStore(args.database, args.catalog)
    actor = f"offline-root:{os.getpid()}"
    try:
        if args.action == "tasks":
            result = store.tasks()
        elif args.action == "rounds":
            result = store.rounds()
        elif args.action == "audit":
            result = store.audit()
        elif args.action.startswith("task-"):
            mapping = {"task-open": "OPEN", "task-lock": "SUBMISSION_LOCKED", "task-score": "SCORED", "task-close": "CLOSED"}
            result = store.transition_task(args.task or "", mapping[args.action], actor, args.duration_seconds)
        elif args.action == "round-assign-simulation-case":
            result = store.assign_round_case(args.round or "", args.case or "", actor, simulation_only=True)
        elif args.action == "round-configure":
            result = store.configure_round(args.round or "", args.champion or "", args.challenger or "", actor)
        elif args.action == "round-judge":
            if args.score is None:
                raise SystemExit("--score is required")
            store.judge(args.round or "", args.team or "", args.judge or "", args.score, args.notes, actor)
            result = {"status": "RECORDED"}
        elif args.action == "round-confirm":
            result = store.confirm_round(args.round or "", actor)
        else:
            mapping = {"round-start": "RUNNING", "round-lock": "SUBMISSION_LOCKED", "round-judging": "JUDGING", "round-close": "CLOSED"}
            result = store.transition_round(args.round or "", mapping[args.action], actor, args.duration_seconds)
    except StorageError as exc:
        raise SystemExit(f"{exc.code}: {exc.message}") from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
