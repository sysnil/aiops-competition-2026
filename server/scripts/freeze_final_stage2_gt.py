#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze Final Stage 2 automatic-GT metadata without copying or modifying GT")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-id", default="FINAL-STAGE2-AUTO-GT-20261019")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("freeze manifest already exists")
    gt_root = Path("/etc/aiops-private-grader/final-gt-preview")
    public_manifest_root = Path("/data/aiops-contest/public/manifests")
    cases = []
    for gt_path in sorted(gt_root.glob("SF-*.json")):
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        case_id = gt_path.stem
        manifest_path = public_manifest_root / f"{case_id}.json"
        candidates = gt.get("events", [])
        merged = gt.get("merged_events", [])
        cases.append({
            "case_id": case_id,
            "gt_path": str(gt_path),
            "gt_sha256": sha(gt_path),
            "gt_status": gt.get("status"),
            "source_public_manifest_sha256": gt.get("source_public_manifest_sha256"),
            "installed_public_manifest_path": str(manifest_path),
            "installed_public_manifest_file_sha256": sha(manifest_path),
            "candidate_count_declared": gt.get("candidate_count"),
            "candidate_count_actual": len(candidates),
            "merged_event_count_declared": gt.get("merged_event_count"),
            "merged_event_count_actual": len(merged),
            "scorer_event_source": "merged_events",
            "scorer_event_count": len(merged),
            "event_overlap_policy": gt.get("event_overlap_policy"),
        })
    output = {
        "batch_id": args.batch_id,
        "classification": "INTERNAL_PREVIEW_ONLY",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scoring_rule_version": "final-stage2-auto-gt-1.0",
        "gt_normalization_version": "final-stage2-auto-gt-frozen-v1",
        "formal_gt_modified": False,
        "formal_gt_approved": False,
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(args.output, 0o440)
    print(json.dumps({"batch_id": args.batch_id, "cases": len(cases), "output": str(args.output)}))


if __name__ == "__main__":
    main()
