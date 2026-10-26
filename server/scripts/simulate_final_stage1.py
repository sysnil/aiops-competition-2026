#!/usr/bin/env python3
"""Replay eight PK rounds in an isolated database with synthetic scores only."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.final_store import FinalStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args()
    workdir = args.workdir.resolve()
    if workdir.exists():
        raise SystemExit("simulation workdir already exists; original replay is immutable")
    workdir.mkdir(parents=True, mode=0o700)
    production = json.loads((Path(__file__).resolve().parents[1] / "config" / "final-stage1-catalog.json").read_text())
    catalog = dict(production)
    catalog["publication_state"] = "INTERNAL_DEVELOPMENT_ONLY"
    catalog["tasks"] = [dict(item) for item in production["tasks"]]
    for item in catalog["tasks"]:
        item["simulation_only"] = True
    catalog_path = workdir / "simulation-catalog.json"
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2))
    store = FinalStore(workdir / "simulation.sqlite3", catalog_path)

    def submit(task_id: str, team_id: str, score: float):
        answer = {"case_id": store.task(task_id)["case_id"], "task_id": task_id, "anomalies": [], "source": "SYNTHETIC_STATE_MACHINE_TEST"}
        row = store.create_submission(task_id, team_id, f"{task_id}-{team_id}-simulation", "f" * 64, answer, preview=True)
        store.set_grade(row["submission_id"], {"status": "PREVIEW_COMPLETED", "score": score, "simulation_only": True})
        return row["submission_id"]

    for index in range(1, 10):
        team = f"TEAM-{index:02d}"
        for task in ("FT-01", "FT-02", "FT-03"):
            submit(task, team, 6 + index / 10)

    champion = "TEAM-01"
    history = []
    for index in range(1, 9):
        round_id = f"PK-R{index:02d}"
        challenger = f"TEAM-{index + 1:02d}"
        if index >= 5:
            reused = ["SF-004", "SF-008", "SF-009", "SF-012"][index - 5]
            store.assign_round_case(round_id, reused, "simulation-controller", simulation_only=True)
        store.configure_round(round_id, champion, challenger, "simulation-controller")
        store.transition_round(round_id, "RUNNING", "simulation-controller", 60)
        submissions = {
            champion: submit(round_id, champion, 6.0),
            challenger: submit(round_id, challenger, 9.0),
        }
        store.transition_round(round_id, "SUBMISSION_LOCKED", "simulation-controller")
        store.transition_round(round_id, "JUDGING", "simulation-controller")
        store.judge(round_id, champion, "JUDGE-01", 60, "synthetic review", "simulation-judge")
        store.judge(round_id, challenger, "JUDGE-01", 90, "synthetic review", "simulation-judge")
        result = store.confirm_round(round_id, "simulation-controller")
        store.transition_round(round_id, "CLOSED", "simulation-controller")
        history.append({
            "round_id": round_id, "case_id": store.task(round_id)["case_id"],
            "champion_before": champion, "challenger": challenger,
            "submission_ids": submissions,
            "winner": result["winner_team_id"],
            "simulation_only": True,
        })
        champion = result["winner_team_id"]
    audit = store.audit(1000)
    output = {
        "status": "SIMULATION_ONLY",
        "official_leaderboard_updated": False,
        "private_gt_read": False,
        "round_count": len(history),
        "final_champion": champion,
        "rounds": history,
        "audit_entries": len(audit),
        "confirmed_results": sum(item["action"] == "ROUND_RESULT_CONFIRMED" for item in audit),
        "formal_case_gap": ["PK-R05", "PK-R06", "PK-R07", "PK-R08"],
    }
    (workdir / "eight-round-replay.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps({key: output[key] for key in ("status", "round_count", "final_champion", "confirmed_results", "formal_case_gap")}))


if __name__ == "__main__":
    main()
