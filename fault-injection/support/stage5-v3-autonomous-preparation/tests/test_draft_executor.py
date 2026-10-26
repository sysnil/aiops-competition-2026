"""Synthetic-only protocol tests; no cluster, real GT, or RCA model is accessed."""

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from draft_executor import (DraftExecutor, ProtocolError, VARIANTS, read_json,
                            load_draft_allocation)


LAYERS = ("SERVICE", "POD", "NODE", "NETWORK_PATH")
ENTITIES = {"SERVICE": "SERVICE/recommendation", "POD": "POD/rec-pod-a",
            "NODE": "NODE/k8s-worker1", "NETWORK_PATH": "NETWORK_PATH/w3/w2"}
METHODS = ("OpenRCA-XL", "MicroIRC-original")


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    return path


def candidates():
    rows = []
    for split, count in (("DEV", 12), ("TEST", 20)):
        for index in range(count):
            rows.append({"id": f"{split}-P{index + 1:02d}", "split": split,
                         "kind": "PRIMARY", "layer": LAYERS[index % 4],
                         "mechanism": "synthetic_mechanism", "seed": 850001 + len(rows),
                         "order": index + 1})
        for layer in LAYERS:
            for order in range(1, 3 if split == "DEV" else 5):
                rows.append({"id": f"{split}-{layer}-R{order}", "split": split,
                             "kind": "RESERVE", "layer": layer,
                             "mechanism": "synthetic_mechanism", "seed": 850001 + len(rows),
                             "order": order})
    return rows


def evidence(root, attempt_id, state):
    return put(root / "evidence" / attempt_id / (state + ".json"),
               {"synthetic": True, "attempt_id": attempt_id, "state": state})


def variants(root, attempt_id, *, bad_b2=False, private_leak=False):
    for variant in VARIANTS:
        items = [ENTITIES[layer] for layer in LAYERS
                 if layer != "NETWORK_PATH" or variant != "A"]
        if variant == "B2" and bad_b2:
            items.remove(ENTITIES["NETWORK_PATH"])
        value = {"attempt_id": attempt_id, "variant": variant,
                 "candidates": items, "evidence_ids": ["metric/1", "trace/1"],
                 "non_network_evidence_sha256": "abc123",
                 "network_context": {"path_id": "w3/w2"} if variant == "B2" else None}
        if private_leak:
            value["root_entity"] = ENTITIES["SERVICE"]
        put(root / "public" / attempt_id / (variant + ".json"), value)


def predictions(root, attempt_id, *, fail_variant=None, false_top1=False,
                hallucinate=False):
    for method in METHODS:
        for variant in VARIANTS:
            rank = [ENTITIES["SERVICE"], ENTITIES["POD"], ENTITIES["NODE"]]
            if variant != "A":
                rank.append(ENTITIES["NETWORK_PATH"])
            if not false_top1 and variant == "B2":
                rank = [ENTITIES["NETWORK_PATH"], *rank[:-1]]
            status = "METHOD_PREREQUISITE_BLOCKED" if method == METHODS[1] else \
                ("INFERENCE_FAILURE" if variant == fail_variant else "SCORED")
            if hallucinate and method == METHODS[0] and variant == "B2":
                rank[0] = "POD/private-target"
            value = {"attempt_id": attempt_id, "method": method,
                     "variant": variant, "status": status,
                     "root_layer": rank[0].split("/", 1)[0] if status == "SCORED" else None,
                     "root_entity": rank[0] if status == "SCORED" else None,
                     "ranking": [{"rank": n, "level": e.split("/", 1)[0], "entity": e}
                                 for n, e in enumerate(rank, 1)] if status == "SCORED" else [],
                     "reason": "synthetic status" if status != "SCORED" else None,
                     "evidence_refs": ["metric/1"] if status == "SCORED" else []}
            put(root / "predictions" / attempt_id / method / (variant + ".json"), value)


def complete(root, *, invalid_pre=None, invalid_gt=None, fail_variant=None,
             none_impact=None, zero_target=None, rows=None,
             no_prediction_at=None):
    invalid_pre = invalid_pre or {}
    invalid_gt = invalid_gt or set()
    none_impact = none_impact or set()
    zero_target = zero_target or set()
    run = DraftExecutor(root)
    candidate_rows = rows if rows is not None else candidates()
    run.register(candidate_rows, METHODS,
        {METHODS[0]: set(LAYERS), METHODS[1]: {"SERVICE"}},
        blocked_methods={METHODS[1]}, test_method_lock_sha256="synthetic-lock")
    gt = {"DEV": {}, "TEST": {}}
    for row in candidate_rows:
        name, split = row["id"], row["split"]
        if name in invalid_pre:
            stage, reason = invalid_pre[name]
            if stage == "preflight":
                run.transition(name, "PREFLIGHT_FAILED",
                               evidence=[evidence(root, name, "preflight_failed")])
            else:
                run.transition(name, "PREFLIGHT_PASSED",
                               evidence=[evidence(root, name, "preflight")])
                run.transition(name, "INJECTION_STARTED",
                               evidence=[evidence(root, name, "injection_started")])
                run.transition(name, "INJECTION_COMPLETED",
                               evidence=[evidence(root, name, "injection_completed")])
                run.transition(name, "CAPTURE_INCOMPLETE",
                               evidence=[evidence(root, name, "capture_incomplete")])
            run.transition(name, "ATTEMPT_INVALID_PRE_GT", reason=reason,
                           safe_cleanup=True,
                           evidence=[evidence(root, name, "terminal")])
            continue
        for state in ("PREFLIGHT_PASSED", "INJECTION_STARTED",
                      "INJECTION_COMPLETED", "CAPTURE_COMPLETED"):
            run.transition(name, state, evidence=[evidence(root, name, state)])
        variants(root, name)
        run.freeze_public(name, [evidence(root, name, "public_provenance")])
        predictions(root, name, fail_variant=fail_variant)
        if no_prediction_at and name == no_prediction_at[0]:
            path = root / "predictions" / name / METHODS[0] / (no_prediction_at[1] + ".json")
            prediction = read_json(path)
            prediction.update(status="NO_PREDICTION", ranking=[],
                              root_layer=None, root_entity=None,
                              reason="synthetic abstention", evidence_refs=[])
            put(path, prediction)
        run.freeze_predictions(name)
        gt[split][name] = {"root_layer": row["layer"],
                           "root_entity": ENTITIES[row["layer"]],
                           "injection_valid": name not in invalid_gt,
                           "observability_valid": True,
                           "independent_public_root_evidence": True,
                           "business_impact": "NONE" if name in none_impact else "STRONG",
                           "target_successes_during": 0 if name in zero_target else 100,
                           "target_server_spans_during": 0 if name in zero_target else 100}
    for split in gt:
        put(root / "private_synthetic_gt" / (split + ".json"),
            {"split": split, "attempts": gt[split]})
    return run


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def partial_public(self):
        run = DraftExecutor(self.root)
        run.register(candidates(), METHODS,
            {METHODS[0]: set(LAYERS), METHODS[1]: {"SERVICE"}},
            blocked_methods={METHODS[1]}, test_method_lock_sha256="synthetic-lock")
        name = "DEV-P01"
        for state in ("PREFLIGHT_PASSED", "INJECTION_STARTED",
                      "INJECTION_COMPLETED", "CAPTURE_COMPLETED"):
            run.transition(name, state, evidence=[evidence(self.root, name, state)])
        return run, name

    def test_fixed_pool_and_early_gt_barrier(self):
        run = DraftExecutor(self.root)
        run.register(candidates(), METHODS,
            {METHODS[0]: set(LAYERS), METHODS[1]: {"SERVICE"}},
            blocked_methods={METHODS[1]}, test_method_lock_sha256="synthetic-lock")
        self.assertEqual(len(run.attempts), 56)
        with self.assertRaises(ProtocolError):
            run.unseal_gt("DEV", self.root / "private_synthetic_gt/DEV.json")
        with self.assertRaises(ProtocolError):
            run.resolve("DEV")
        with self.assertRaises(ProtocolError):
            run.register(candidates(), METHODS, {}, test_method_lock_sha256="x")

    def test_primary_priority_reserve_order_and_post_gt_invalid(self):
        run = complete(self.root, invalid_pre={
            "DEV-P01": ("preflight", "PREFLIGHT_FAILED"),
            "DEV-P02": ("capture", "CAPTURE_INCOMPLETE")},
            invalid_gt={"DEV-P03"})
        run.resolve("DEV")
        run.unseal_gt("DEV", self.root / "private_synthetic_gt/DEV.json")
        for name in read_json(self.root / "private_synthetic_gt/DEV.json")["attempts"]:
            run.admit("DEV", name, self.root / "private_synthetic_gt/DEV.json")
        chosen = run.select("DEV")
        self.assertEqual(chosen["DEV-P01"], "DEV-SERVICE-R1")
        self.assertEqual(chosen["DEV-P02"], "DEV-POD-R1")
        self.assertEqual(chosen["DEV-P03"], "DEV-NODE-R1")
        self.assertEqual(chosen["DEV-P05"], "DEV-P05")
        with self.assertRaises(ProtocolError):
            run.transition("DEV-P01", "PREFLIGHT_PASSED",
                           evidence=[evidence(self.root, "DEV-P01", "late")])

    def test_variant_failure_coverage_and_network_scope(self):
        run = complete(self.root, fail_variant="B1", none_impact={"DEV-P01"},
                       zero_target={"DEV-P04"})
        run.resolve("DEV")
        run.unseal_gt("DEV", self.root / "private_synthetic_gt/DEV.json")
        for name in read_json(self.root / "private_synthetic_gt/DEV.json")["attempts"]:
            row = run.admit("DEV", name, self.root / "private_synthetic_gt/DEV.json")
            if name in ("DEV-P01", "DEV-P04"):
                self.assertTrue(row["root_observable"])
            if name == "DEV-P01":
                self.assertFalse(row["user_impactful"])
        run.select("DEV")
        score = run.score("DEV")
        self.assertEqual(score["OpenRCA-XL/A"]["applicable"], 9)
        self.assertEqual(score["OpenRCA-XL/A"]["status_counts"]["OUT_OF_CANDIDATE_SPACE"], 3)
        self.assertEqual(score["OpenRCA-XL/B1"]["prediction_coverage"], 0)
        self.assertEqual(score["OpenRCA-XL/B1"]["coverage_adjusted_exact_at_1"], 0)
        self.assertEqual(score["OpenRCA-XL/B2"]["prediction_coverage"], 1)
        self.assertEqual(score["OpenRCA-XL/B2"]["strata"]["layer"]["NETWORK_PATH"]
                         ["applicable"], 3)
        self.assertEqual(score["paired_comparisons"]["OpenRCA-XL/A_to_B1"]
                         ["applicable_paired_cases"], 9)
        self.assertEqual(score["paired_comparisons"]["OpenRCA-XL/B1_to_B2"]
                         ["applicable_paired_cases"], 12)
        self.assertEqual(score["paired_comparisons"]["OpenRCA-XL/B1_to_B2"]
                         ["both_valid_predictions"], 0)
        self.assertIsNone(score["MicroIRC-original/A"]["prediction_coverage"])
        self.assertEqual(DraftExecutor(self.root).scores["DEV"]["methods"], score)

    def test_exhausted_reserves_shortfall(self):
        bad = {"DEV-P01", "DEV-SERVICE-R1", "DEV-SERVICE-R2"}
        run = complete(self.root, invalid_gt=bad)
        run.resolve("DEV")
        run.unseal_gt("DEV", self.root / "private_synthetic_gt/DEV.json")
        for name in read_json(self.root / "private_synthetic_gt/DEV.json")["attempts"]:
            run.admit("DEV", name, self.root / "private_synthetic_gt/DEV.json")
        self.assertIsNone(run.select("DEV")["DEV-P01"])

    def test_reserve_selection_uses_registered_order_not_file_position(self):
        rows = candidates()
        first = next(i for i, row in enumerate(rows)
                     if row["id"] == "DEV-SERVICE-R1")
        second = next(i for i, row in enumerate(rows)
                      if row["id"] == "DEV-SERVICE-R2")
        rows[first], rows[second] = rows[second], rows[first]
        run = complete(self.root, rows=rows, invalid_gt={"DEV-P01"})
        run.resolve("DEV")
        gt = self.root / "private_synthetic_gt/DEV.json"
        run.unseal_gt("DEV", gt)
        for name in read_json(gt)["attempts"]:
            run.admit("DEV", name, gt)
        self.assertEqual(run.select("DEV")["DEV-P01"], "DEV-SERVICE-R1")

    def test_hash_mismatch_blocks_recovery_and_unseal(self):
        run = complete(self.root)
        run.resolve("DEV")
        source = self.root / "public/DEV-P01/A.json"
        source.write_text(source.read_text() + " ")
        with self.assertRaises(ProtocolError):
            DraftExecutor(self.root)
        with self.assertRaises(ProtocolError):
            run.unseal_gt("DEV", self.root / "private_synthetic_gt/DEV.json")

    def test_illegal_prediction_and_variant_are_rejected(self):
        run = DraftExecutor(self.root)
        run.register(candidates(), METHODS,
            {METHODS[0]: set(LAYERS), METHODS[1]: {"SERVICE"}},
            blocked_methods={METHODS[1]}, test_method_lock_sha256="synthetic-lock")
        name = "DEV-P01"
        for state in ("PREFLIGHT_PASSED", "INJECTION_STARTED",
                      "INJECTION_COMPLETED", "CAPTURE_COMPLETED"):
            run.transition(name, state, evidence=[evidence(self.root, name, state)])
        variants(self.root, name, bad_b2=True)
        with self.assertRaises(ProtocolError):
            run.freeze_public(name, [evidence(self.root, name, "public")])
        variants(self.root, name)
        run.freeze_public(name, [evidence(self.root, name, "public")])
        predictions(self.root, name, hallucinate=True)
        with self.assertRaises(ProtocolError):
            run.freeze_predictions(name)
        predictions(self.root, name)
        run.freeze_predictions(name)
        with self.assertRaises(ProtocolError):
            run.freeze_predictions(name)

    def test_public_private_boundary_and_safe_abort(self):
        run = DraftExecutor(self.root)
        run.register(candidates(), METHODS,
            {METHODS[0]: set(LAYERS), METHODS[1]: {"SERVICE"}},
            blocked_methods={METHODS[1]}, test_method_lock_sha256="synthetic-lock")
        private = put(self.root / "private_synthetic_gt/DEV.json", {"secret": True})
        with self.assertRaises(ProtocolError):
            run.transition("DEV-P01", "PREFLIGHT_PASSED", evidence=[private])
        run.transition("DEV-P01", "PREFLIGHT_PASSED",
                       evidence=[evidence(self.root, "DEV-P01", "pre")])
        run.transition("DEV-P01", "INJECTION_STARTED",
                       evidence=[evidence(self.root, "DEV-P01", "start")])
        run.transition("DEV-P01", "INJECTION_FAILED",
                       evidence=[evidence(self.root, "DEV-P01", "failed")])
        run.transition("DEV-P01", "ATTEMPT_INVALID_PRE_GT",
                       reason="CLEANUP_OR_RECOVERY_FAILED", safe_cleanup=False,
                       evidence=[evidence(self.root, "DEV-P01", "unsafe")])
        self.assertEqual(run.splits["DEV"], "SPLIT_ABORTED")
        with self.assertRaises(ProtocolError):
            run.unseal_gt("DEV", private)

    def test_interrupted_recovery_and_test_split_independence(self):
        run = DraftExecutor(self.root)
        run.register(candidates(), METHODS,
            {METHODS[0]: set(LAYERS), METHODS[1]: {"SERVICE"}},
            blocked_methods={METHODS[1]}, test_method_lock_sha256="synthetic-lock")
        run.transition("DEV-P01", "PREFLIGHT_PASSED",
                       evidence=[evidence(self.root, "DEV-P01", "pre")])
        reloaded = DraftExecutor(self.root)
        self.assertEqual(reloaded.attempts["DEV-P01"]["state"], "PREFLIGHT_PASSED")
        self.assertEqual(reloaded.splits["TEST"], "COLLECTING")
        with self.assertRaises(ProtocolError):
            reloaded.transition("DEV-P01", "PREFLIGHT_PASSED",
                evidence=[evidence(self.root, "DEV-P01", "duplicate")])

    def test_reviewed_draft_allocation_is_32_plus_24(self):
        path = Path(__file__).resolve().parents[1] / "design/dataset-allocation-draft.yaml"
        rows = load_draft_allocation(path)
        self.assertEqual(len(rows), 56)
        self.assertEqual(sum(r["kind"] == "PRIMARY" for r in rows), 32)
        self.assertEqual(sum(r["kind"] == "RESERVE" for r in rows), 24)
        self.assertEqual(rows[0]["id"], "S5V3-D01")
        self.assertEqual(rows[-1]["id"], "S5V3-TW-R04")
        run = DraftExecutor(self.root)
        run.register(rows, METHODS,
            {METHODS[0]: set(LAYERS), METHODS[1]: {"SERVICE"}},
            blocked_methods={METHODS[1]}, test_method_lock_sha256="synthetic-lock")

    def test_real_execution_mode_unavailable(self):
        with self.assertRaises(ProtocolError):
            DraftExecutor(self.root, synthetic=False)

    def test_invalid_pool_size_is_rejected(self):
        with self.assertRaises(ProtocolError):
            DraftExecutor(self.root).register(candidates()[:-1], METHODS,
                {METHODS[0]: set(LAYERS), METHODS[1]: {"SERVICE"}},
                test_method_lock_sha256="synthetic-lock")

    def test_b1_b2_candidate_mismatch_is_rejected(self):
        run, name = self.partial_public()
        variants(self.root, name, bad_b2=True)
        with self.assertRaises(ProtocolError):
            run.freeze_public(name, [evidence(self.root, name, "public")])

    def test_public_label_leak_is_rejected(self):
        run, name = self.partial_public()
        variants(self.root, name, private_leak=True)
        with self.assertRaises(ProtocolError):
            run.freeze_public(name, [evidence(self.root, name, "public")])

    def test_unverified_evidence_reference_is_rejected(self):
        run, name = self.partial_public()
        variants(self.root, name)
        run.freeze_public(name, [evidence(self.root, name, "public")])
        predictions(self.root, name)
        file = self.root / "predictions" / name / METHODS[0] / "B2.json"
        value = read_json(file)
        value["evidence_refs"] = ["private/injection"]
        put(file, value)
        with self.assertRaises(ProtocolError):
            run.freeze_predictions(name)

    def test_method_prerequisite_cannot_be_relabelled_from_failure(self):
        run, name = self.partial_public()
        variants(self.root, name)
        run.freeze_public(name, [evidence(self.root, name, "public")])
        predictions(self.root, name)
        file = self.root / "predictions" / name / METHODS[0] / "A.json"
        value = read_json(file)
        value.update(status="METHOD_PREREQUISITE_BLOCKED", ranking=[],
                     root_layer=None, root_entity=None,
                     reason="invented prerequisite")
        put(file, value)
        with self.assertRaises(ProtocolError):
            run.freeze_predictions(name)

    def test_prediction_extra_private_field_is_rejected(self):
        run, name = self.partial_public()
        variants(self.root, name)
        run.freeze_public(name, [evidence(self.root, name, "public")])
        predictions(self.root, name)
        file = self.root / "predictions" / name / METHODS[0] / "A.json"
        value = read_json(file)
        value["fault_target"] = "POD/private"
        put(file, value)
        with self.assertRaises(ProtocolError):
            run.freeze_predictions(name)

    def test_prediction_archive_is_immutable_by_hash(self):
        run, name = self.partial_public()
        variants(self.root, name)
        run.freeze_public(name, [evidence(self.root, name, "public")])
        predictions(self.root, name)
        run.freeze_predictions(name)
        file = self.root / "predictions" / name / METHODS[0] / "A.json"
        file.write_text(file.read_text() + " ")
        with self.assertRaises(ProtocolError):
            DraftExecutor(self.root)

    def test_gt_unseal_is_per_split(self):
        run = complete(self.root)
        run.resolve("DEV")
        run.unseal_gt("DEV", self.root / "private_synthetic_gt/DEV.json")
        self.assertEqual(run.splits["TEST"], "COLLECTING")
        with self.assertRaises(ProtocolError):
            run.unseal_gt("TEST", self.root / "private_synthetic_gt/TEST.json")

    def test_invalid_terminal_reason_and_missing_cleanup_are_rejected(self):
        run = DraftExecutor(self.root)
        run.register(candidates(), METHODS,
            {METHODS[0]: set(LAYERS), METHODS[1]: {"SERVICE"}},
            blocked_methods={METHODS[1]}, test_method_lock_sha256="synthetic-lock")
        run.transition("DEV-P01", "PREFLIGHT_FAILED",
                       evidence=[evidence(self.root, "DEV-P01", "failed")])
        with self.assertRaises(ProtocolError):
            run.transition("DEV-P01", "ATTEMPT_INVALID_PRE_GT",
                reason="CAPTURE_INCOMPLETE", safe_cleanup=True,
                evidence=[evidence(self.root, "DEV-P01", "bad_reason")])
        with self.assertRaises(ProtocolError):
            run.transition("DEV-P01", "ATTEMPT_INVALID_PRE_GT",
                reason="PREFLIGHT_FAILED", safe_cleanup=False,
                evidence=[evidence(self.root, "DEV-P01", "bad_cleanup")])

    def test_no_prediction_remains_in_coverage_denominator(self):
        run = complete(self.root, no_prediction_at=("DEV-P01", "A"))
        run.resolve("DEV")
        gt = self.root / "private_synthetic_gt/DEV.json"
        run.unseal_gt("DEV", gt)
        for name in read_json(gt)["attempts"]:
            run.admit("DEV", name, gt)
        run.select("DEV")
        row = run.score("DEV")["OpenRCA-XL/A"]
        self.assertEqual(row["applicable"], 9)
        self.assertEqual(row["valid_predictions"], 8)
        self.assertEqual(row["status_counts"]["NO_PREDICTION"], 1)

    def test_different_gt_file_cannot_replace_unsealed_gt(self):
        run = complete(self.root)
        run.resolve("DEV")
        gt = self.root / "private_synthetic_gt/DEV.json"
        run.unseal_gt("DEV", gt)
        other = put(self.root / "private_synthetic_gt/replacement.json", read_json(gt))
        with self.assertRaises(ProtocolError):
            run.admit("DEV", "DEV-P01", other)


if __name__ == "__main__":
    unittest.main()
