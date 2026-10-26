#!/usr/bin/env python3
"""Explicit synthetic-only CLI for the append-only Stage5-v3 DRAFT executor."""

import argparse
import json
from pathlib import Path

from draft_executor import DraftExecutor, ProtocolError, load_draft_allocation


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    action = parser.add_subparsers(dest="action", required=True)
    action.add_parser("init")
    register = action.add_parser("register")
    register.add_argument("--allocation", type=Path, required=True)
    register.add_argument("--method-scope", type=Path, required=True)
    move = action.add_parser("transition")
    move.add_argument("--attempt", required=True)
    move.add_argument("--to", required=True)
    move.add_argument("--evidence", type=Path, action="append", required=True)
    move.add_argument("--reason")
    move.add_argument("--safe-cleanup", action="store_true")
    for name in ("freeze-public", "freeze-predictions"):
        sub = action.add_parser(name)
        sub.add_argument("--attempt", required=True)
        if name == "freeze-public":
            sub.add_argument("--evidence", type=Path, action="append", required=True)
    abort = action.add_parser("abort")
    abort.add_argument("--split", choices=("DEV", "TEST"), required=True)
    abort.add_argument("--reason", required=True)
    abort.add_argument("--evidence", type=Path, action="append", required=True)
    for name in ("resolve", "select", "score"):
        sub = action.add_parser(name)
        sub.add_argument("--split", choices=("DEV", "TEST"), required=True)
    for name in ("unseal", "admit"):
        sub = action.add_parser(name)
        sub.add_argument("--split", choices=("DEV", "TEST"), required=True)
        sub.add_argument("--synthetic-gt", type=Path, required=True)
        if name == "admit":
            sub.add_argument("--attempt", required=True)
    action.add_parser("status")
    args = parser.parse_args()
    root = args.root.resolve()
    marker = root / ".synthetic-only"
    if args.action == "init":
        if root.exists() and any(root.iterdir()):
            raise ProtocolError("Synthetic archive root must be new and empty")
        root.mkdir(parents=True, exist_ok=True)
        marker.write_text("SYNTHETIC_DRY_RUN_ONLY\n")
        run = DraftExecutor(root)
    else:
        if not marker.is_file() or marker.read_text() != "SYNTHETIC_DRY_RUN_ONLY\n":
            raise ProtocolError("CLI refuses an archive without its synthetic-only marker")
        run = DraftExecutor(root)
    result = None
    if args.action == "register":
        scope = json.loads(args.method_scope.read_text())
        if scope.get("mode") != "SYNTHETIC_DRY_RUN":
            raise ProtocolError("Only synthetic method scopes are accepted")
        result = run.register(load_draft_allocation(args.allocation),
            scope["methods"], {m: set(x) for m, x in scope["method_layers"].items()},
            blocked_methods=scope["blocked_methods"],
            test_method_lock_sha256=scope["test_method_lock_sha256"])
    elif args.action == "transition":
        result = run.transition(args.attempt, args.to, evidence=args.evidence,
            reason=args.reason, safe_cleanup=args.safe_cleanup)
    elif args.action == "freeze-public":
        result = run.freeze_public(args.attempt, args.evidence)
    elif args.action == "freeze-predictions":
        result = run.freeze_predictions(args.attempt)
    elif args.action == "abort":
        result = run.abort(args.split, args.reason, args.evidence)
    elif args.action == "resolve":
        result = run.resolve(args.split)
    elif args.action == "unseal":
        result = run.unseal_gt(args.split, args.synthetic_gt)
    elif args.action == "admit":
        result = run.admit(args.split, args.attempt, args.synthetic_gt)
    elif args.action == "select":
        result = run.select(args.split)
    elif args.action == "score":
        result = run.score(args.split)
    print(json.dumps({"mode": "SYNTHETIC_DRY_RUN", "action": args.action,
        "result": result, "event_count": run.sequence,
        "event_chain_head_sha256": run.last_hash,
        "split_states": run.splits}, sort_keys=True))


if __name__ == "__main__":
    main()
