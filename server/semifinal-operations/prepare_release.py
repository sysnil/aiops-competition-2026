"""Promote the specifically reviewed QA GT without altering scoring content."""

import argparse
import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from activate_semifinal import atomic_json, digest, load


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    catalog_path = Path("/opt/aiops-contest-server/config/questions.json")
    registry_path = Path("/etc/aiops-contest/team_registry.json")
    state_path = Path("/etc/aiops-contest/competition_state.json")
    plan_path = Path("/root/semifinal-20261022-preparation/semifinal-20261022-plan.json")
    gt_root = Path("/etc/aiops-private-grader/qa-gt")
    preview_root = Path("/etc/aiops-private-grader/qa-gt-preview")
    manifest_path = Path("/etc/aiops-contest/semifinal-20261022-release.json")
    router_path = Path("/opt/aiops-contest-server/grading/submission_api/router.py")
    review = load(args.review)
    if digest(catalog_path) != review["catalog_sha256"]:
        raise ValueError("reviewed question catalog changed")
    if manifest_path.exists() or args.archive.exists():
        raise ValueError("release/archive already exists; do not overwrite")
    expected = {f"Q{i:02d}" for i in range(1, 11)}
    if {q["question_id"] for q in review["questions"]} != expected or len(review["questions"]) != 10:
        raise ValueError("review must contain exactly ten questions")
    state, registry, catalog, plan = (load(p) for p in (state_path, registry_path, catalog_path, plan_path))
    if any(s["state"] != "NOT_OPEN" for s in state["semifinal"]["sessions"].values()):
        raise ValueError("all semifinal sessions must remain NOT_OPEN during preparation")
    teams = {t["team_id"]: t for t in registry["teams"] if t["team_id"].startswith("TEAM-")}
    if set(teams) != {f"TEAM-{i:02d}" for i in range(1, 18)}:
        raise ValueError("incomplete roster")
    for number in range(1, 18):
        team = teams[f"TEAM-{number:02d}"]
        if team["assigned_ip"] != f"192.168.51.{51 + number}" or team["session"] != ("AM" if number <= 9 else "PM"):
            raise ValueError("roster mapping mismatch")
    if plan["date"] != "2026-10-22" or plan["timezone"] != "Asia/Shanghai" or set(plan["questions"]) != expected:
        raise ValueError("unexpected scoring plan")
    stamp = datetime.now(timezone.utc).isoformat()
    promoted = []
    for item in review["questions"]:
        source = preview_root / (item["question_id"] + ".json")
        target = gt_root / source.name
        if source.is_symlink() or digest(source) != item["preview_gt_sha256"] or target.exists():
            raise ValueError("reviewed GT changed or formal GT already exists")
        original = load(source)
        if original["review_status"] != "DRAFT_PENDING_HUMAN_REVIEW" or original["classification"] != "DRAFT_PREVIEW_ONLY":
            raise ValueError("unexpected source GT status")
        public_manifest = Path("/data/aiops-contest/public/manifests") / (original["case_id"] + ".json")
        public = load(public_manifest)
        if public.get("source_manifest_sha256") != original["generation"]["source_public_manifest_sha256"]:
            raise ValueError("source case manifest mismatch")
        if len(original["answers"]) != 3:
            raise ValueError("exactly three answers required")
        value = copy.deepcopy(original)
        value.update(review_status="APPROVED", classification="FORMAL_APPROVED",
                     gt_version=f"semifinal-qa-formal-20261022-v1/{original['question_id']}")
        value["approval"] = {"authority": "USER_APPROVED_20261021", "approved_at_utc": stamp,
                             "source_preview_sha256": digest(source), "source_gt_version": original["gt_version"]}
        # Only approval/version metadata may change, never answers, charts or tolerances.
        check = copy.deepcopy(value)
        check.pop("approval")
        for field in ("review_status", "classification", "gt_version"):
            check[field] = original[field]
        if check != original:
            raise ValueError("scoring content changed during promotion")
        promoted.append((source, target, value, public_manifest))
    print(json.dumps({"status": "VERIFIED", "gt_count": len(promoted), "sessions_remain_closed": True,
                      "approval": "USER_APPROVED_20261021"}))
    if not args.apply:
        return
    args.archive.mkdir(mode=0o700, parents=True)
    for path in (catalog_path, registry_path, state_path, plan_path, args.review):
        shutil.copy2(path, args.archive / path.name)
    shutil.copytree(preview_root, args.archive / "original-preview-gt")
    owner = gt_root.stat()
    pinned = []
    for source, target, value, public_manifest in promoted:
        atomic_json(target, value, mode=0o640, uid=owner.st_uid, gid=owner.st_gid)
        for kind, path in (("approved_gt", target), ("original_preview_gt", source), ("public_manifest", public_manifest)):
            if str(path) not in {x["path"] for x in pinned}:
                pinned.append({"kind": kind, "path": str(path), "sha256": digest(path)})
    for question in catalog["questions"]:
        if question["question_id"] in expected:
            if question["publication_status"] != "SEMIFINAL_PREVIEW":
                raise ValueError("unexpected question publication status")
            question["publication_status"] = "SEMIFINAL_PUBLISHED"
    atomic_json(catalog_path, catalog)
    for team in teams.values():
        team["allowed_modes"] = sorted(set(team["allowed_modes"]) | {"SEMIFINAL"})
    registry["updated_at"] = stamp
    atomic_json(registry_path, registry)
    state["semifinal"]["published_cases"] = sorted({value["case_id"] for _, _, value, _ in promoted})
    for session, window in plan["windows"].items():
        zone = ZoneInfo(plan["timezone"])
        start = datetime.fromisoformat(plan["date"] + "T" + window["start"]).replace(tzinfo=zone)
        end = datetime.fromisoformat(plan["date"] + "T" + window["end"]).replace(tzinfo=zone)
        record = state["semifinal"]["sessions"][session]
        record.update(opens_at=start.astimezone(timezone.utc).isoformat(), closes_at=None,
                      scoring_closes_at=end.astimezone(timezone.utc).isoformat(), read_after_scoring_close=True)
    state["updated_at"] = stamp
    atomic_json(state_path, state)
    plan.update(status="APPROVED_FOR_SCORING", approved_at_utc=stamp, qa_submission_attempt_limit=None)
    atomic_json(plan_path, plan)
    for kind, path in (("question_catalog", catalog_path), ("scoring_plan", plan_path), ("unlimited_qa_router", router_path), ("activation_code", Path(__file__).with_name("activate_semifinal.py"))):
        pinned.append({"kind": kind, "path": str(path), "sha256": digest(path)})
    manifest = {"version": "SEMIFINAL-FORMAL-20261022-v1", "approval": "USER_APPROVED_20261021",
                "created_at_utc": stamp, "review_sha256": digest(args.review), "pinned_files": pinned,
                "plan_path": str(plan_path), "registry_path": str(registry_path), "state_path": str(state_path),
                "catalog_path": str(catalog_path), "gt_directory": str(gt_root), "audit_directory": str(args.archive),
                "unlimited_qa_attempts": True, "historical_scores_regraded": False}
    atomic_json(manifest_path, manifest, mode=0o640, uid=registry_path.stat().st_uid, gid=registry_path.stat().st_gid)
    print(json.dumps({"status": "PREPARED_NOT_OPEN", "manifest": str(manifest_path), "sha256": digest(manifest_path)}))


if __name__ == "__main__":
    main()
