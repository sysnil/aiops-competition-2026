"""Register approved Final cases into still-closed task slots on 51."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PREBOUND = {"FT-01", "FT-02", "FT-03", "PK-R01", "PK-R02", "PK-R03", "PK-R04"}
VACANT = {"PK-R05", "PK-R06", "PK-R07", "PK-R08"}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"not an object: {path}")
    return value


def audit(plan: dict, catalog_path: Path, database_path: Path, gt_root: Path,
          public_root: Path, registry_path: Path, state_path: Path) -> dict:
    mapping = plan["tasks"]
    reserve = plan["reserve_case_id"]
    if (set(mapping) != PREBOUND | VACANT or len(set(mapping.values())) != len(mapping) or
            reserve in mapping.values() or len(set(mapping.values()) | {reserve}) != 12):
        raise ValueError("Final task/reserve allocation is not 11+1 distinct cases")
    manifest_path = gt_root / "gt-manifest.json"
    raw_manifest = manifest_path.read_bytes()
    if sha(raw_manifest) != plan["gt_manifest_sha256"]:
        raise ValueError("approved GT manifest SHA differs from allocation")
    gt_manifest = json.loads(raw_manifest)
    gt_cases = {row["case_id"]: row for row in gt_manifest["cases"]}
    if set(gt_cases) != set(mapping.values()) | {reserve}:
        raise ValueError("approved GT case universe differs from allocation")
    for case_id, row in gt_cases.items():
        gt_raw = (gt_root / row["file"]).read_bytes()
        gt = json.loads(gt_raw)
        public = read_json(public_root / "manifests" / f"{case_id}.json")
        if (sha(gt_raw) != row["sha256"] or gt["version"] != gt_manifest["version"] or
                gt.get("status") != "APPROVED" or gt.get("review_status") != "APPROVED" or
                gt.get("source_public_manifest_sha256") != public.get("source_manifest_sha256") or
                public.get("publication_status") != "published"):
            raise ValueError(f"approved private/public binding failed: {case_id}")
    catalog = read_json(catalog_path)
    tasks = {row["task_id"]: row for row in catalog["tasks"]}
    if set(tasks) != set(mapping):
        raise ValueError("catalog task set changed")
    for task_id in PREBOUND:
        if tasks[task_id]["case_id"] != mapping[task_id]:
            raise ValueError(f"existing task binding would change: {task_id}")
    for task_id in VACANT:
        if tasks[task_id]["case_id"] is not None:
            raise ValueError(f"vacant task is already bound: {task_id}")
    teams = read_json(registry_path)["teams"]
    if any("FINAL" in row.get("allowed_modes", []) or "SEMIFINAL" in row.get("allowed_modes", [])
           for row in teams if str(row.get("team_id", "")).startswith("TEAM-")):
        raise ValueError("participant competition access is still enabled")
    state = read_json(state_path)
    if (any(row["state"] != "NOT_OPEN" for row in state["semifinal"]["sessions"].values()) or
            state["final"]["team_preview_test"]["enabled"]):
        raise ValueError("competition session or Final preview remains open")
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT task_id,case_id,status FROM final_tasks").fetchall()
        rounds = connection.execute("SELECT round_id,status,simulation_only FROM final_rounds").fetchall()
        official = connection.execute("SELECT count(*) FROM final_submissions WHERE grading_mode='OFFICIAL'").fetchone()[0]
        if official:
            raise ValueError("official Final submissions already exist")
        stored = {row[0]: row for row in rows}
        if set(stored) != set(mapping) or any(row[2] != "NOT_OPEN" for row in rows):
            raise ValueError("Final task state is not closed")
        if any(row[1] != "NOT_OPEN" or row[2] for row in rounds):
            raise ValueError("PK round is open or simulation-only")
        for task_id in PREBOUND:
            if stored[task_id][1] != mapping[task_id]:
                raise ValueError(f"database binding would change: {task_id}")
        for task_id in VACANT:
            if stored[task_id][1] is not None:
                raise ValueError(f"database slot is already bound: {task_id}")
    finally:
        connection.close()
    return {"catalog": catalog, "approved_cases": len(gt_cases),
            "registered_slots": len(PREBOUND), "vacant_slots": sorted(VACANT),
            "reserve": reserve}


def apply(plan: dict, catalog_path: Path, database_path: Path, result: dict,
          allocation_path: Path) -> None:
    if allocation_path.exists():
        raise FileExistsError(allocation_path)
    backup = catalog_path.with_name("final-stage1-catalog.before-final-allocation-20261020.json")
    if backup.exists():
        raise FileExistsError(backup)
    catalog = result["catalog"]
    for task in catalog["tasks"]:
        if task["task_id"] in VACANT:
            task["case_id"] = plan["tasks"][task["task_id"]]
    serialized = (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode()
    shutil.copy2(catalog_path, backup)
    fd, temporary = tempfile.mkstemp(prefix=".final-catalog-", dir=catalog_path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, catalog_path.stat().st_mode & 0o777)
        connection = sqlite3.connect(database_path, timeout=10)
        try:
            connection.execute("BEGIN IMMEDIATE")
            for task_id in sorted(VACANT):
                case_id = plan["tasks"][task_id]
                cursor = connection.execute(
                    "UPDATE final_tasks SET case_id=? WHERE task_id=? AND case_id IS NULL AND status='NOT_OPEN'",
                    (case_id, task_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError(f"task changed during registration: {task_id}")
                connection.execute(
                    "INSERT INTO final_audit(at,actor,action,object_id,details_json) VALUES (?,?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(), "committee-root",
                     "FORMAL_CASE_REGISTERED", task_id,
                     json.dumps({"case_id": case_id, "gt_manifest_sha256": plan["gt_manifest_sha256"]})),
                )
            os.replace(temporary, catalog_path)
            connection.commit()
        except Exception:
            connection.rollback()
            shutil.copy2(backup, catalog_path)
            raise
        finally:
            connection.close()
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    fd = os.open(allocation_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o640)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    catalog_path = Path("/opt/aiops-contest-server/config/final-stage1-catalog.json")
    database_path = Path("/var/lib/aiops-grading/submissions.sqlite3")
    allocation_path = Path("/opt/aiops-contest-server/config/final-case-allocation-20261020.json")
    plan = read_json(args.plan)
    result = audit(plan, catalog_path, database_path,
                   Path("/etc/aiops-private-grader/final-gt-v2-20261020"),
                   Path("/data/aiops-contest/public"),
                   Path("/etc/aiops-contest/team_registry.json"),
                   Path("/etc/aiops-contest/competition_state.json"))
    if args.apply:
        apply(plan, catalog_path, database_path, result, allocation_path)
    print(json.dumps({"status": "REGISTERED_CLOSED" if args.apply else "VALIDATED_ONLY",
                      "approved_cases": result["approved_cases"],
                      "qualifier": [plan["tasks"][f"FT-{index:02d}"] for index in (1, 2, 3)],
                      "new_pk_bindings": {key: plan["tasks"][key] for key in sorted(VACANT)},
                      "reserve": result["reserve"]}, sort_keys=True))


if __name__ == "__main__":
    main()
