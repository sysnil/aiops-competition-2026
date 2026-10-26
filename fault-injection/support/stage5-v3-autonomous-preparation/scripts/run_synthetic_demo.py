#!/usr/bin/env python3
"""Persist a complete 56-candidate synthetic DEV/TEST execution archive."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "tests"))
from test_draft_executor import complete
from draft_executor import DraftExecutor, ProtocolError, read_json, load_draft_allocation


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Synthetic demo archive must be new")
    allocation = HERE / "design/dataset-allocation-draft.yaml"
    run = complete(args.output,
        rows=load_draft_allocation(allocation),
        invalid_pre={"S5V3-D01": ("preflight", "PREFLIGHT_FAILED"),
                     "S5V3-T04": ("capture", "CAPTURE_INCOMPLETE")},
        invalid_gt={"S5V3-D02"}, fail_variant="B1",
        none_impact={"S5V3-D05", "S5V3-T05"},
        zero_target={"S5V3-D08", "S5V3-T08"})
    snapshots = {}
    for split in ("DEV", "TEST"):
        run.resolve(split)
        gt = args.output / "private_synthetic_gt" / (split + ".json")
        run.unseal_gt(split, gt)
        for name in read_json(gt)["attempts"]:
            run.admit(split, name, gt)
        chosen = run.select(split)
        scored = run.score(split)
        snapshots[split] = {"selection": chosen,
            "shortfalls": sum(item is None for item in chosen.values()),
            "scores": scored}
    if DraftExecutor(args.output).last_hash != run.last_hash:
        raise ProtocolError("Replay changed the synthetic event-chain head")
    result = {"mode": "SYNTHETIC_DRY_RUN", "attempts": len(run.attempts),
              "draft_allocation_sha256": hashlib.sha256(allocation.read_bytes()).hexdigest(),
              "split_states": run.splits, "events": run.sequence,
              "event_chain_head_sha256": run.last_hash, "splits": snapshots,
              "real_faults": 0, "real_gt_reads": 0, "formal_predictions": 0}
    path = args.output / "demo-summary.json"
    with path.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": "SYNTHETIC_E2E_PASS",
                      "attempts": result["attempts"], "events": run.sequence,
                      "head_sha256": run.last_hash,
                      "summary_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
