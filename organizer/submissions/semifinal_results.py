"""Read-only, time-bounded semifinal ranking with per-submission provenance."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo


TERMINAL_FAILURE = {"FAILED", "REJECTED", "VALIDATION_FAILED", "ERROR", "INFERENCE_FAILURE"}
SELECTION = "HIGHEST_SCORE_EARLIEST_RECEIVED_WITHIN_WINDOW"
TIES = "EARLIEST_TOTAL_ACHIEVEMENT_ELAPSED_FROM_SESSION_START"
RANKING_VERSION = "SEMIFINAL-BEST-SCORE-SPEED-2.1-MANUAL-START"


def instant(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("submission timestamp must include timezone")
    return result.astimezone(timezone.utc)


def validate_plan(plan: dict) -> dict[str, tuple[str, datetime, datetime]]:
    if plan["selection"] != SELECTION or plan.get("ties") != TIES:
        raise ValueError("unsupported selection rule")
    questions = plan["questions"]
    if len(questions) != len(set(questions)) or not questions:
        raise ValueError("invalid question list")
    assigned = {}
    for session, window in plan["windows"].items():
        zone = ZoneInfo(plan["timezone"])
        if window.get("schedule_status") == "AWAITING_ORGANIZER_START":
            if window.get("start") is not None:
                raise ValueError("held session must not have a scoring start")
            start = end = None
        else:
            start = datetime.fromisoformat(plan["date"] + "T" + window["start"]).replace(tzinfo=zone).astimezone(timezone.utc)
            end = datetime.fromisoformat(plan["date"] + "T" + window["end"]).replace(tzinfo=zone).astimezone(timezone.utc)
            if end <= start:
                raise ValueError("invalid scoring window")
        for team in window["teams"]:
            if team in assigned:
                raise ValueError("team assigned more than once")
            assigned[team] = (session, start, end)
    if set(assigned) != {f"TEAM-{i:02d}" for i in range(1, 18)}:
        raise ValueError("all 17 teams must be assigned exactly once")
    return assigned


def elapsed_microseconds(received: datetime, start: datetime) -> int:
    duration = received - start
    return (duration.days * 86400 + duration.seconds) * 1_000_000 + duration.microseconds


def display_elapsed(microseconds: int | None) -> str:
    if microseconds is None:
        return "—"
    seconds, fraction = divmod(microseconds, 1_000_000)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{fraction:06d}"


def standings(entries: list[dict], rank_field: str = "rank") -> list[dict]:
    # Team ID orders display only; exact score/time ties retain a shared rank.
    def key(item):
        return (-Decimal(str(item["total_score"])), item["total_score_elapsed_microseconds"] or 0)
    result = sorted(entries, key=lambda item: (*key(item), item["team_id"]))
    for index, item in enumerate(result):
        item[rank_field] = result[index-1][rank_field] if index and key(item) == key(result[index-1]) else index+1
    return result


def rank_rows(rows: list[dict], plan: dict, now: datetime | None = None) -> dict:
    assigned = validate_plan(plan)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must include timezone")
    questions = plan["questions"]
    selected = {}
    audit = []
    unresolved = []
    for row in rows:
        item = {k: row.get(k) for k in ("submission_id", "team_id", "question_id", "received_at", "status", "grading_mode", "competition_mode", "total_score", "session", "rubric_version", "gt_version_identifier", "answer_sha256")}
        team, question = row.get("team_id"), row.get("question_id")
        reason = None
        if team not in assigned:
            reason = "NOT_COMPETITION_TEAM"
        elif row.get("competition_mode") != "SEMIFINAL" or row.get("task_type") != "SEMIFINAL_QUERY_VISUALIZATION":
            reason = "NOT_SEMIFINAL_QA"
        elif row.get("grading_mode") != "OFFICIAL":
            reason = "NOT_OFFICIAL"
        elif question not in questions:
            reason = "NOT_ASSIGNED_QUESTION"
        else:
            session, start, end = assigned[team]
            received = instant(row["received_at"])
            if row.get("session") != session:
                reason = "SESSION_MISMATCH"
            elif start is None or end is None:
                reason = "SESSION_NOT_STARTED"
            elif not start <= received < end:
                reason = "OUTSIDE_SCORING_WINDOW"
            elif received > now:
                reason = "FUTURE_TIMESTAMP"
            else:
                score = row.get("total_score")
                if row.get("status") != "COMPLETED":
                    reason = "NO_COMPLETED_SCORE"
                    if row.get("status") not in TERMINAL_FAILURE:
                        unresolved.append(row["submission_id"])
                elif isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= plan["points_per_question"]:
                    reason = "INVALID_SCORE"
                    unresolved.append(row["submission_id"])
                else:
                    key = (team, question)
                    previous = selected.get(key)
                    # Minimise this key: highest score, then earliest receipt.
                    candidate = (-Decimal(str(score)), received, row["submission_id"])
                    if previous is None or candidate < previous[0]:
                        selected[key] = (candidate, row)
        item["exclusion_reason"] = reason
        audit.append(item)
    chosen = {item[1]["submission_id"] for item in selected.values()}
    for item in audit:
        item["selected"] = item["submission_id"] in chosen
        if item["exclusion_reason"] is None and not item["selected"]:
            best = selected[(item["team_id"], item["question_id"])][1]
            item["exclusion_reason"] = ("LOWER_THAN_BEST_SCORE" if Decimal(str(item["total_score"])) < Decimal(str(best["total_score"]))
                                        else "EQUAL_BEST_SCORE_NOT_EARLIEST_SELECTED_RECEIPT")
    leaderboard = []
    for team, (session, start, _) in sorted(assigned.items()):
        entry = {"team_id": team, "session": session, "schedule_status": "NOT_STARTED" if start is None else "SCHEDULED"}
        scores = []
        positive_receipts = []
        for question in questions:
            row = selected.get((team, question))
            score = Decimal(str(row[1]["total_score"])) if row else Decimal(0)
            scores.append(score)
            entry[question] = float(score)
            entry[question + "_submission_id"] = row[1]["submission_id"] if row else None
            received = instant(row[1]["received_at"]) if row else None
            elapsed = elapsed_microseconds(received, start) if received else None
            entry[question + "_received_at_utc"] = received.isoformat() if received else None
            entry[question + "_elapsed_microseconds"] = elapsed
            entry[question + "_elapsed_seconds"] = elapsed / 1_000_000 if elapsed is not None else None
            if score > 0:
                positive_receipts.append(received)
        entry["total_score"] = float(sum(scores))
        entry["maximum_score"] = len(questions) * plan["points_per_question"]
        entry["scored_questions"] = sum((team, q) in selected for q in questions)
        # Zero points are the common initial state; submitting zero later must
        # neither win a speed tie nor delay an already achieved positive total.
        achieved = max(positive_receipts) if positive_receipts else None
        elapsed = elapsed_microseconds(achieved, start) if achieved else None
        entry["total_score_achieved_at_utc"] = achieved.isoformat() if achieved else None
        entry["total_score_elapsed_microseconds"] = elapsed
        entry["total_score_elapsed_seconds"] = elapsed / 1_000_000 if elapsed is not None else None
        entry["total_score_elapsed_display"] = display_elapsed(elapsed)
        entry["speed_status"] = "POSITIVE_TOTAL_ACHIEVED" if achieved else "NO_POSITIVE_SCORE"
        leaderboard.append(entry)
    leaderboard = standings(leaderboard)
    for session in plan["windows"]:
        standings([item for item in leaderboard if item["session"] == session], "session_rank")
    ended = all(window[2] is not None and now >= window[2] for window in assigned.values())
    approved = plan.get("status") == "APPROVED_FOR_SCORING"
    row_teams = {row["submission_id"]: row.get("team_id") for row in rows}
    session_summaries = {}
    for session, window in plan["windows"].items():
        pending = sorted(sid for sid in unresolved if row_teams[sid] in window["teams"])
        session_started = all(assigned[team][1] is not None for team in window["teams"])
        session_ended = all(assigned[team][2] is not None and now >= assigned[team][2] for team in window["teams"])
        session_summaries[session] = {"status": "NOT_STARTED" if not session_started else "COMPLETE" if approved and session_ended and not pending else "PROVISIONAL",
                                      "team_count": len(window["teams"]), "pending_or_unresolved_submission_ids": pending}
    return {"status": "COMPLETE" if approved and ended and not unresolved else "PROVISIONAL",
            "ranking_version": RANKING_VERSION,
            "plan_status": plan.get("status"),
            "plan_version": plan["version"], "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
            "time_rule": "start <= server received_at < end", "selection": plan["selection"], "ties": plan["ties"],
            "speed_rule": "elapsed from own session start to latest of the earliest receipts achieving each positive per-question maximum; exact score/time ties share rank",
            "session_summaries": session_summaries,
            "pending_or_unresolved_submission_ids": sorted(unresolved),
            "leaderboard": leaderboard, "submission_audit": audit}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=Path("/var/lib/aiops-submission-snapshot/submissions.sqlite3"))
    parser.add_argument("--output", type=Path, required=True, help="new directory; never overwrites an earlier ranking")
    parser.add_argument("--require-complete", action="store_true", help="refuse a final export until both sessions end and eligible submissions are settled")
    args = parser.parse_args()
    plan_raw = args.plan.read_bytes()
    plan = json.loads(plan_raw)
    if (datetime.now(timezone.utc).timestamp() - args.database.stat().st_mtime) > 30:
        raise SystemExit("SCORING_SNAPSHOT_STALE: refresh the committee snapshot before ranking")
    uri = f"file:{quote(str(args.database.resolve()), safe='/')}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        rows = [dict(row) for row in connection.execute("SELECT * FROM submissions ORDER BY submission_id")]
    result = rank_rows(rows, plan)
    result["plan_sha256"] = hashlib.sha256(plan_raw).hexdigest()
    result["input_rows_sha256"] = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result["input_snapshot"] = str(args.database.resolve())
    if args.require_complete and result["status"] != "COMPLETE":
        raise SystemExit("RANKING_NOT_FINAL: match still running or eligible submissions are unresolved; use provisional export to inspect details")
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    (args.output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (args.output / "ranking-plan.json").write_bytes(plan_raw)
    (args.output / "input-rows.json").write_text(json.dumps(rows, sort_keys=True, separators=(",", ":")))
    fields = ["rank", "session_rank", "team_id", "session", "total_score", "maximum_score", "scored_questions",
              "total_score_elapsed_display", "total_score_elapsed_microseconds", "total_score_achieved_at_utc"]
    fields += plan["questions"] + [q + "_submission_id" for q in plan["questions"]]
    for scope in ("ALL", *plan["windows"]):
        name = "leaderboard.csv" if scope == "ALL" else f"leaderboard-{scope}.csv"
        entries = result["leaderboard"] if scope == "ALL" else [r for r in result["leaderboard"] if r["session"] == scope]
        with (args.output / name).open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(entries)
    with (args.output / "best-submissions.csv").open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, ["team_id", "session", "question_id", "score", "submission_id", "received_at_utc", "elapsed_microseconds"])
        writer.writeheader()
        for item in result["leaderboard"]:
            for question in plan["questions"]:
                writer.writerow({"team_id": item["team_id"], "session": item["session"], "question_id": question,
                                 "score": item[question], "submission_id": item[question+"_submission_id"],
                                 "received_at_utc": item[question+"_received_at_utc"], "elapsed_microseconds": item[question+"_elapsed_microseconds"]})
    print(json.dumps({"status": result["status"], "teams": len(result["leaderboard"]), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
