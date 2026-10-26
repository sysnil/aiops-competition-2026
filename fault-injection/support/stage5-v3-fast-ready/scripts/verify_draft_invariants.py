#!/usr/bin/env python3
"""Read-only regression of frozen-boundary Stage5-v3 DRAFT invariants."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path("/root/stage5-v3-cross-layer-rca/design")
OUTPUT = Path("/root/stage5-v3-fast-ready/results/draft-invariants.json")
FILES = {name: ROOT / (name + "-draft.yaml") for name in (
    "dataset-allocation", "evaluation-protocol", "admission-rules")}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUTPUT.exists():
        raise ValueError("Refusing overwrite")
    data = {name: yaml.safe_load(path.read_text()) for name, path in FILES.items()}
    allocation, evaluation, admission = [data[name] for name in (
        "dataset-allocation", "evaluation-protocol", "admission-rules")]
    primary = allocation["proposed_primary_order"]
    reserves = allocation["proposed_reserve_order"]
    layer_counts = Counter(item["layer"] for item in primary)
    variants = evaluation["variants"]
    checks = {
        "primary_32_four_layer_balance": allocation["primary_slots"] == len(primary) == 32
            and set(layer_counts.values()) == {8},
        "dev_12_test_20": Counter(item["split"] for item in primary) ==
            {"DEV": 12, "TEST": 20},
        "reserves_24_registered": allocation["registered_reserves_total"] ==
            len(reserves) == 24 and Counter(item["split"] for item in reserves) ==
            {"DEV": 8, "TEST": 16},
        "A_B1_B2_candidate_invariants": set(variants) == {"A", "B1", "B2"}
            and variants["A"]["network_path_candidate"] is False and
            variants["B2"]["network_path_candidate"] == "exactly_same_ids_as_B1",
        "public_private_and_prediction_gt_barrier": evaluation[
            "public_private_boundary"]["prediction_frozen_before_gt"] is True
            and "evaluator_gt" in evaluation["public_private_boundary"][
                "algorithm_denied"],
        "GT_unsealed_after_split_prediction_freeze": evaluation["blind_sequence"].index(
            "unseal_DEV_GT_then_decide_all_admissions_without_prediction_content") >
            evaluation["blind_sequence"].index(
                "freeze_all_DEV_public_bundles_method_statuses_predictions_and_split_resolution")
            and evaluation["blind_sequence"].index(
                "unseal_TEST_GT_then_decide_all_admissions_without_prediction_content") >
            evaluation["blind_sequence"].index(
                "freeze_all_TEST_public_bundles_method_statuses_predictions_and_split_resolution"),
        "method_failure_not_reserve_trigger": evaluation["methods"][
            "failures_cannot_activate_reserve"] is True and admission[
            "decision_model"]["method_failure_invalidates_case"] is False,
        "admission_independent_of_prediction": admission["decision_model"][
            "algorithm_prediction_used_in_admission"] is False,
        "root_observable_and_user_impactful_separate": admission["decision_model"][
            "subset_relation"] == "USER_IMPACTFUL_subset_of_ROOT_OBSERVABLE",
        "reserve_selection_after_GT_only": allocation["reserves"][
            "no_new_injection_after_GT"] is True and allocation["reserves"][
            "cannot_replace_due_to_prediction_or_difficulty"] is True,
        "draft_not_frozen": all(item["document_status"] == "DRAFT_NOT_FROZEN"
            for item in data.values()),
    }
    value = {"status": "DRAFT_INVARIANTS_PASS" if all(checks.values()) else
        "DRAFT_INVARIANTS_FAIL", "at_utc": datetime.now(timezone.utc).isoformat(),
        "checks": checks, "blockers": [key for key, passed in checks.items() if not passed],
        "primary_layer_counts": dict(layer_counts),
        "source_sha256": {str(path): sha(path) for path in FILES.values()},
        "read_only": True, "test_gt_read": False, "fault_injected": False}
    OUTPUT.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "blockers": value["blockers"]}))
    if value["status"] != "DRAFT_INVARIANTS_PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
