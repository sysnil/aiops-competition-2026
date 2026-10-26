"""Fail-closed, one-way semifinal opening; never closes read access."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def atomic_json(path: Path, value: dict, *, mode: int | None = None, uid: int | None = None, gid: int | None = None) -> None:
    if path.is_symlink():
        raise ValueError("refusing symlink destination")
    previous = path.stat() if path.exists() else None
    fd, name = tempfile.mkstemp(prefix=".semifinal-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode if mode is not None else (previous.st_mode & 0o777 if previous else 0o640))
        if uid is not None or previous is not None:
            os.chown(temporary, uid if uid is not None else previous.st_uid,
                     gid if gid is not None else previous.st_gid)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def preflight(manifest_path: Path, expected_sha: str) -> tuple[dict, dict, dict]:
    if digest(manifest_path) != expected_sha:
        raise ValueError("release manifest hash mismatch")
    manifest = load(manifest_path)
    if manifest["approval"] != "USER_APPROVED_20261021":
        raise ValueError("release is not approved")
    for item in manifest["pinned_files"]:
        if digest(Path(item["path"])) != item["sha256"]:
            raise ValueError("release prerequisite file changed: " + item["kind"])
    plan = load(Path(manifest["plan_path"]))
    if plan.get("status") != "APPROVED_FOR_SCORING" or len(set(plan["questions"])) != 10:
        raise ValueError("scoring plan is not approved")
    registry = load(Path(manifest["registry_path"]))
    actual = {r["team_id"]: r for r in registry["teams"] if r["team_id"].startswith("TEAM-")}
    expected = {team: session for session, window in plan["windows"].items() for team in window["teams"]}
    if (set(plan["windows"]) != {"AM", "PM"}
            or sum(len(w["teams"]) for w in plan["windows"].values()) != 17
            or set(expected) != {f"TEAM-{i:02d}" for i in range(1, 18)}
            or len(expected) != 17 or set(actual) != set(expected)):
        raise ValueError("roster is incomplete")
    for team, session in expected.items():
        record = actual[team]
        if (record["session"] != session or "SEMIFINAL" not in record["allowed_modes"]
                or record["assigned_ip"] != f"192.168.51.{51 + int(team[-2:])}"):
            raise ValueError("team/session/IP authorization mismatch")
    catalog = load(Path(manifest["catalog_path"]))
    questions = {q["question_id"]: q for q in catalog["questions"]}
    state = load(Path(manifest["state_path"]))
    for question in plan["questions"]:
        q = questions[question]
        gt = load(Path(manifest["gt_directory"]) / (question + ".json"))
        if (q["publication_status"] != "SEMIFINAL_PUBLISHED" or gt["review_status"] != "APPROVED"
                or gt["question_id"] != question or gt["case_id"] != q["case_id"]
                or q["case_id"] not in state["semifinal"]["published_cases"]):
            raise ValueError("question/GT/publication mismatch")
    return manifest, plan, state


def open_session(manifest_path: Path, expected_sha: str, session: str, *, now: datetime | None = None, check_only: bool = False) -> dict:
    manifest, plan, state = preflight(manifest_path, expected_sha)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timezone is required")
    window = plan["windows"][session]
    if window.get("schedule_status") == "AWAITING_ORGANIZER_START":
        if check_only:
            return {"status": "AWAITING_ORGANIZER_START", "session": session, "current_state": state["semifinal"]["sessions"][session]["state"], "opens_at": None}
        raise ValueError("session start requires a new organizer instruction and confirmed scoring start")
    start = datetime.fromisoformat(plan["date"] + "T" + window["start"]).replace(tzinfo=ZoneInfo(plan["timezone"]))
    end = datetime.fromisoformat(plan["date"] + "T" + window["end"]).replace(tzinfo=ZoneInfo(plan["timezone"]))
    current = state["semifinal"]["sessions"][session]
    if current["state"] not in {"NOT_OPEN", "OPEN"}:
        raise ValueError("manual close or emergency lock must not be overridden")
    if current["opens_at"] != start.astimezone(timezone.utc).isoformat():
        raise ValueError("scheduled start changed")
    if check_only:
        return {"status": "PREREQUISITES_PASS", "session": session, "opens_at": start.isoformat(), "scoring_ends_at": end.isoformat(), "current_state": current["state"]}
    if now < start:
        raise ValueError("opening before the registered start is forbidden")
    # Serialize concurrent attempts and preserve the other session's state.
    lock_path = Path(manifest["audit_directory"]) / "activation.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest, plan, state = preflight(manifest_path, expected_sha)
        current = state["semifinal"]["sessions"][session]
        if current["state"] not in {"NOT_OPEN", "OPEN"}:
            raise ValueError("manual close or emergency lock must not be overridden")
        if current["state"] == "OPEN":
            return {"status": "ALREADY_OPEN", "session": session}
        backup = Path(manifest["audit_directory"]) / f"state-before-open-{session}.json"
        if backup.exists():
            raise ValueError("opening backup already exists; inspect previous activation")
        atomic_json(backup, state, mode=0o600)
        current["state"] = "OPEN"
        state["updated_at"] = now.astimezone(timezone.utc).isoformat()
        atomic_json(Path(manifest["state_path"]), state)
        event = {"at_utc": now.astimezone(timezone.utc).isoformat(), "action": "OPEN_SESSION", "session": session,
                 "manifest_sha256": expected_sha, "read_after_end": True, "scoring_end": end.isoformat()}
        with (Path(manifest["audit_directory"]) / "activation-audit.jsonl").open("a") as output:
            output.write(json.dumps(event, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())
    return {"status": "OPEN", "session": session}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--session", choices=["AM", "PM"], required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(open_session(args.manifest, args.manifest_sha256, args.session, check_only=args.check_only)))
