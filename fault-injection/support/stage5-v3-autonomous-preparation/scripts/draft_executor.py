"""Synthetic-only, append-only Stage5-v3 protocol executor prototype.

This module has no Kubernetes, fault-adapter, model, or real-GT interface.
It intentionally requires a synthetic archive root and synthetic GT files.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import yaml


LAYERS = {"SERVICE", "POD", "NODE", "NETWORK_PATH"}
VARIANTS = ("A", "B1", "B2")
RUN_STATUSES = {"SCORED", "NO_PREDICTION", "INFERENCE_FAILURE",
                "METHOD_PREREQUISITE_BLOCKED"}
TERMINAL_REASONS = {"PREFLIGHT_FAILED", "SAFETY_STOP_NOT_STARTED",
    "INJECTION_FAILED", "CAPTURE_INCOMPLETE", "PUBLIC_CONVERSION_FAILED",
    "PUBLIC_PRIVATE_LEAK", "PREDICTION_ARCHIVE_INFRA_FAILURE",
    "CLEANUP_OR_RECOVERY_FAILED"}
TRANSITIONS = {
    "REGISTERED": {"PREFLIGHT_PASSED", "PREFLIGHT_FAILED", "ATTEMPT_INVALID_PRE_GT"},
    "PREFLIGHT_PASSED": {"INJECTION_STARTED", "ATTEMPT_INVALID_PRE_GT"},
    "PREFLIGHT_FAILED": {"ATTEMPT_INVALID_PRE_GT"},
    "INJECTION_STARTED": {"INJECTION_COMPLETED", "INJECTION_FAILED"},
    "INJECTION_FAILED": {"ATTEMPT_INVALID_PRE_GT"},
    "INJECTION_COMPLETED": {"CAPTURE_COMPLETED", "CAPTURE_INCOMPLETE",
                            "ATTEMPT_INVALID_PRE_GT"},
    "CAPTURE_INCOMPLETE": {"ATTEMPT_INVALID_PRE_GT"},
    "CAPTURE_COMPLETED": {"PUBLIC_BUNDLE_FROZEN", "ATTEMPT_INVALID_PRE_GT"},
    "PUBLIC_BUNDLE_FROZEN": {"PREDICTIONS_FROZEN", "ATTEMPT_INVALID_PRE_GT"},
}
TERMINAL_FROM = {
    "REGISTERED": {"SAFETY_STOP_NOT_STARTED"},
    "PREFLIGHT_FAILED": {"PREFLIGHT_FAILED"},
    "PREFLIGHT_PASSED": {"SAFETY_STOP_NOT_STARTED"},
    "INJECTION_FAILED": {"INJECTION_FAILED", "CLEANUP_OR_RECOVERY_FAILED"},
    "INJECTION_COMPLETED": {"CLEANUP_OR_RECOVERY_FAILED"},
    "CAPTURE_INCOMPLETE": {"CAPTURE_INCOMPLETE"},
    "CAPTURE_COMPLETED": {"PUBLIC_CONVERSION_FAILED", "PUBLIC_PRIVATE_LEAK"},
    "PUBLIC_BUNDLE_FROZEN": {"PREDICTION_ARCHIVE_INFRA_FAILURE"},
}


class ProtocolError(ValueError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(path.read_text())


PRIVATE_KEYS = {"gt", "root_layer", "root_entity", "fault_type", "fault_target",
                "injection_command", "private_adapter_logs", "prediction_correctness",
                "root_rank", "model_score"}


def reject_private_keys(value):
    if isinstance(value, dict):
        if PRIVATE_KEYS & set(value):
            raise ProtocolError("Private label or model outcome in pre-GT evidence")
        for child in value.values():
            reject_private_keys(child)
    elif isinstance(value, list):
        for child in value:
            reject_private_keys(child)


def load_draft_allocation(path):
    """Read candidate identities only; this never opens a case or GT archive."""
    allocation = yaml.safe_load(Path(path).read_text())
    if allocation.get("document_status") != "DRAFT_NOT_FROZEN" or \
            allocation.get("execution_model") != "FIXED_PRIMARY_AND_RESERVE_POOL_PRE_GT":
        raise ProtocolError("Not the reviewed DRAFT fixed-pool allocation")
    primaries = allocation["proposed_primary_order"]
    reserves = allocation["proposed_reserve_order"]
    rows = []
    for split in ("DEV", "TEST"):
        for order, item in enumerate((x for x in primaries if x["split"] == split), 1):
            rows.append({"id": item["id"], "split": split, "kind": "PRIMARY",
                         "layer": item["layer"], "mechanism": item["mechanism"],
                         "seed": item["seed"], "order": order})
        for item in (x for x in reserves if x["split"] == split):
            rows.append({"id": item["id"], "split": split, "kind": "RESERVE",
                         "layer": item["layer"], "mechanism": item["mechanism"],
                         "seed": item["seed"], "order": item["order"]})
    return rows


class DraftExecutor:
    def __init__(self, root: Path, *, synthetic=True):
        if not synthetic:
            raise ProtocolError("Only synthetic/dry-run execution is implemented")
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.events = self.root / "events"
        self.events.mkdir(exist_ok=True)
        self.registry = None
        self.attempts = {}
        self.splits = {"DEV": "COLLECTING", "TEST": "COLLECTING"}
        self.admissions = {}
        self.selection = {}
        self.scores = {}
        self.gt_paths = {}
        self.last_hash = "0" * 64
        self.sequence = 0
        self._replay()

    def _contained(self, path, prefix):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root / prefix) or not path.is_file():
            raise ProtocolError("Evidence outside the synthetic role directory")
        return path

    def _hash_inputs(self, paths):
        result = {}
        for path in paths:
            path = Path(path).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                raise ProtocolError("Missing or external input evidence")
            result[str(path.relative_to(self.root))] = sha(path.read_bytes())
        return result

    def _emit(self, kind, payload, inputs=()):
        record = {"sequence": self.sequence, "kind": kind,
                  "at_utc": datetime.now(timezone.utc).isoformat(),
                  "previous_sha256": self.last_hash, "inputs": self._hash_inputs(inputs),
                  "payload": payload}
        data = canonical(record)
        path = self.events / f"{self.sequence:05d}-{kind}.json"
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        self._apply(record)
        self.last_hash = sha(data)
        self.sequence += 1
        return self.last_hash

    def _replay(self):
        files = sorted(self.events.glob("*.json"))
        for index, path in enumerate(files):
            data = path.read_bytes()
            record = json.loads(data)
            if (record.get("sequence") != index or
                    path.name != f"{index:05d}-{record.get('kind')}.json" or
                    record.get("previous_sha256") != self.last_hash or
                    data != canonical(record)):
                raise ProtocolError("Event ordering or hash chain is damaged")
            for relative, expected in record.get("inputs", {}).items():
                source = (self.root / relative).resolve()
                if not source.is_relative_to(self.root) or not source.is_file() or \
                        sha(source.read_bytes()) != expected:
                    raise ProtocolError("Frozen input hash mismatch: " + relative)
            self._apply(record)
            self.last_hash = sha(data)
            self.sequence += 1

    def _apply(self, record):
        kind, item = record["kind"], record["payload"]
        if kind == "REGISTER":
            self.registry = item
            self.attempts = {row["id"]: {**row, "state": "REGISTERED"}
                             for row in item["attempts"]}
        elif kind == "ATTEMPT":
            attempt = self.attempts[item["id"]]
            attempt["state"] = item["to"]
            if item.get("reason"):
                attempt["reason"] = item["reason"]
        elif kind == "SPLIT":
            self.splits[item["split"]] = item["to"]
            if item["to"] == "GT_UNSEALED":
                self.gt_paths[item["split"]] = item["synthetic_gt_path"]
        elif kind == "ADMISSION":
            self.admissions[item["id"]] = item
            self.attempts[item["id"]]["state"] = item["status"]
        elif kind == "SELECTION":
            self.selection[item["split"]] = item["slots"]
            self.splits[item["split"]] = "SELECTION_FROZEN"
        elif kind == "SCORES":
            self.scores[item["split"]] = item
            self.splits[item["split"]] = "SCORES_FROZEN"
        else:
            raise ProtocolError("Unknown event kind")

    def register(self, attempts, methods, method_layers, blocked_methods=(),
                 method_entities=None,
                 test_method_lock_sha256=None):
        if self.registry is not None or self.sequence:
            raise ProtocolError("Candidate pool already registered")
        if len(attempts) != 56 or len({r["id"] for r in attempts}) != 56:
            raise ProtocolError("The DRAFT pool needs 32 Primary + 24 Reserve")
        counts = Counter((r["split"], r["kind"]) for r in attempts)
        if counts != {("DEV", "PRIMARY"): 12, ("DEV", "RESERVE"): 8,
                      ("TEST", "PRIMARY"): 20, ("TEST", "RESERVE"): 16}:
            raise ProtocolError("Split allocation differs from the DRAFT")
        layers = Counter((r["split"], r["kind"], r["layer"]) for r in attempts)
        if any(layers[split, kind, layer] != expected
               for split, kind, expected in (("DEV", "PRIMARY", 3),
                                             ("DEV", "RESERVE", 2),
                                             ("TEST", "PRIMARY", 5),
                                             ("TEST", "RESERVE", 4))
               for layer in LAYERS):
            raise ProtocolError("Layer allocation differs from the DRAFT")
        if (not methods or set(method_layers) != set(methods) or
                (method_entities is not None and set(method_entities) != set(methods)) or
                not set(blocked_methods) <= set(methods) or
                not test_method_lock_sha256):
            raise ProtocolError("Method scope or TEST lock is absent")
        if any(not set(layers) <= LAYERS for layers in method_layers.values()):
            raise ProtocolError("Method layer scope contains an unknown layer")
        for row in attempts:
            if row["layer"] not in LAYERS or row["split"] not in self.splits or \
                    row["kind"] not in {"PRIMARY", "RESERVE"}:
                raise ProtocolError("Invalid candidate registration")
        if len({row["seed"] for row in attempts}) != 56:
            raise ProtocolError("Candidate seeds must be unique and fixed")
        for split in ("DEV", "TEST"):
            primaries = [row["order"] for row in attempts if
                         row["split"] == split and row["kind"] == "PRIMARY"]
            if sorted(primaries) != list(range(1, len(primaries) + 1)):
                raise ProtocolError("Primary order is not contiguous")
            for layer in LAYERS:
                reserves = [row["order"] for row in attempts if
                            row["split"] == split and row["kind"] == "RESERVE" and
                            row["layer"] == layer]
                if sorted(reserves) != list(range(1, len(reserves) + 1)):
                    raise ProtocolError("Reserve order is not contiguous")
        payload = {"attempts": attempts, "methods": list(methods),
                   "method_layers": {m: sorted(method_layers[m]) for m in methods},
                   "method_entities": {m: sorted(method_entities[m]) for m in methods}
                       if method_entities is not None else None,
                   "blocked_methods": sorted(blocked_methods),
                   "test_method_lock_sha256": test_method_lock_sha256,
                   "mode": "SYNTHETIC_DRY_RUN"}
        return self._emit("REGISTER", payload)

    def transition(self, attempt_id, to, *, actor="engineering_validator",
                   evidence=(), reason=None, safe_cleanup=None):
        attempt = self.attempts.get(attempt_id)
        if attempt is None or self.splits[attempt["split"]] != "COLLECTING":
            raise ProtocolError("Candidate is absent or split is locked")
        old = attempt["state"]
        if to not in TRANSITIONS.get(old, set()):
            raise ProtocolError("Illegal or duplicate attempt transition")
        if actor not in {"engineering_validator", "prediction_validator"}:
            raise ProtocolError("Only pre-GT validators can advance attempts")
        if not evidence:
            raise ProtocolError("A transition requires immutable evidence")
        allowed = {"evidence"}
        if to == "PUBLIC_BUNDLE_FROZEN":
            allowed.add("public")
        if to == "PREDICTIONS_FROZEN":
            allowed = {"predictions"}
        for path in evidence:
            source = Path(path).resolve()
            if (not source.is_relative_to(self.root) or
                    source.relative_to(self.root).parts[0] not in allowed):
                raise ProtocolError("Pre-GT evidence crossed a role boundary")
            if source.is_relative_to(self.root / "evidence"):
                reject_private_keys(read_json(source))
        if to == "ATTEMPT_INVALID_PRE_GT":
            cleanup_ok = safe_cleanup is True or (
                reason == "CLEANUP_OR_RECOVERY_FAILED" and safe_cleanup is False)
            if reason not in TERMINAL_FROM.get(old, set()) or not cleanup_ok:
                raise ProtocolError("Terminal lacks allowed reason and cleanup proof")
        elif reason is not None:
            raise ProtocolError("Nonterminal transition cannot carry failure reason")
        if to == "PUBLIC_BUNDLE_FROZEN":
            self._validate_variants(attempt_id)
        if to == "PREDICTIONS_FROZEN":
            if actor != "prediction_validator":
                raise ProtocolError("Prediction freeze uses a separate validator")
            self._validate_predictions(attempt_id)
        if reason == "CLEANUP_OR_RECOVERY_FAILED":
            # The terminal record is archived, then the split must abort.
            digest = self._emit("ATTEMPT", {"id": attempt_id, "from": old, "to": to,
                "reason": reason, "safe_cleanup": False}, evidence)
            self.abort(attempt["split"], "unsafe recovery", evidence)
            return digest
        return self._emit("ATTEMPT", {"id": attempt_id, "from": old, "to": to,
                                    "reason": reason, "safe_cleanup": safe_cleanup}, evidence)

    def _validate_variants(self, attempt_id):
        variants = {}
        for variant in VARIANTS:
            path = self._contained(self.root / "public" / attempt_id /
                                   (variant + ".json"), "public")
            value = read_json(path)
            if value.get("variant") != variant or value.get("attempt_id") != attempt_id:
                raise ProtocolError("Variant identity mismatch")
            candidates = value.get("candidates")
            if not isinstance(candidates, list) or len(candidates) != len(set(candidates)):
                raise ProtocolError("Candidate list is missing or duplicated")
            if any(not isinstance(x, str) or x.split("/", 1)[0] not in LAYERS
                   for x in candidates):
                raise ProtocolError("Candidate schema invalid")
            if not isinstance(value.get("evidence_ids"), list) or \
                    not value.get("non_network_evidence_sha256"):
                raise ProtocolError("Public evidence provenance missing")
            reject_private_keys(value)
            variants[variant] = value
        a, b1, b2 = (variants[v] for v in VARIANTS)
        if (any(x.startswith("NETWORK_PATH/") for x in a["candidates"]) or
                not set(a["candidates"]) <= set(b1["candidates"]) or
                b1["candidates"] != b2["candidates"] or
                len({v["non_network_evidence_sha256"] for v in variants.values()}) != 1 or
                a.get("network_context") or b1.get("network_context") or
                not b2.get("network_context")):
            raise ProtocolError("A/B1/B2 candidate or evidence invariant failed")

    def _validate_predictions(self, attempt_id):
        for method in self.registry["methods"]:
            for variant in VARIANTS:
                path = self._contained(self.root / "predictions" / attempt_id /
                    method / (variant + ".json"), "predictions")
                run = read_json(path)
                public = read_json(self.root / "public" / attempt_id /
                                   (variant + ".json"))
                status = run.get("status")
                allowed_run_fields = {"attempt_id", "method", "variant", "status",
                                      "root_layer", "root_entity", "ranking",
                                      "reason", "evidence_refs"}
                if set(run) != allowed_run_fields:
                    raise ProtocolError("Prediction has missing or private extra fields")
                if (run.get("attempt_id") != attempt_id or
                        run.get("method") != method or run.get("variant") != variant or
                        status not in RUN_STATUSES):
                    raise ProtocolError("Prediction schema or identity invalid")
                if (status == "METHOD_PREREQUISITE_BLOCKED") != \
                        (method in self.registry["blocked_methods"]):
                    raise ProtocolError("Prerequisite status not predeclared")
                ranking = run.get("ranking")
                if not isinstance(ranking, list):
                    raise ProtocolError("Prediction ranking must be a list")
                if status == "SCORED":
                    if not ranking or len({r.get("entity") for r in ranking}) != len(ranking):
                        raise ProtocolError("Nonempty unique ranking required")
                    if (run.get("root_layer") != ranking[0].get("level") or
                            run.get("root_entity") != ranking[0].get("entity")):
                        raise ProtocolError("Root layer/entity differs from ranking Top1")
                    for index, row in enumerate(ranking, 1):
                        if (not {"rank", "level", "entity"} <= set(row) or
                                not set(row) <= {"rank", "level", "entity",
                                                 "raw_entity", "raw_score"} or
                                (row.get("raw_score") is not None and
                                 not isinstance(row["raw_score"], (int, float)))):
                            raise ProtocolError("Ranking item schema invalid")
                        if (row.get("rank") != index or
                                row.get("entity") not in public["candidates"] or
                                row.get("level") != row["entity"].split("/", 1)[0]):
                            raise ProtocolError("Prediction is outside public candidates")
                elif ranking or not run.get("reason") or \
                        run.get("root_layer") is not None or \
                        run.get("root_entity") is not None:
                    raise ProtocolError("Failure status needs empty ranking and reason")
                if not set(run.get("evidence_refs", [])) <= set(public["evidence_ids"]):
                    raise ProtocolError("Prediction cites private or absent evidence")

    def _bundle_paths(self, attempt_id):
        return [self.root / "public" / attempt_id / (v + ".json") for v in VARIANTS]

    def _prediction_paths(self, attempt_id):
        return [self.root / "predictions" / attempt_id / m / (v + ".json")
                for m in self.registry["methods"] for v in VARIANTS]

    def freeze_public(self, attempt_id, evidence):
        return self.transition(attempt_id, "PUBLIC_BUNDLE_FROZEN",
                               evidence=[*evidence, *self._bundle_paths(attempt_id)])

    def freeze_predictions(self, attempt_id):
        return self.transition(attempt_id, "PREDICTIONS_FROZEN",
            actor="prediction_validator", evidence=self._prediction_paths(attempt_id))

    def abort(self, split, reason, evidence):
        if self.splits.get(split) != "COLLECTING" or not reason:
            raise ProtocolError("Only collecting split can abort")
        return self._emit("SPLIT", {"split": split, "to": "SPLIT_ABORTED",
                                    "reason": reason}, evidence)

    def resolve(self, split):
        if self.splits.get(split) != "COLLECTING":
            raise ProtocolError("Split is not collecting")
        rows = [r for r in self.attempts.values() if r["split"] == split]
        if any(r["state"] not in {"PREDICTIONS_FROZEN", "ATTEMPT_INVALID_PRE_GT"}
               for r in rows):
            raise ProtocolError("Unresolved fixed candidate blocks GT")
        if any(r.get("reason") == "CLEANUP_OR_RECOVERY_FAILED" for r in rows):
            raise ProtocolError("Unsafe cleanup requires split abort")
        self._replay_integrity()
        return self._emit("SPLIT", {"split": split, "to": "SPLIT_RESOLVED",
                                    "candidate_count": len(rows)})

    def _replay_integrity(self):
        DraftExecutor(self.root)

    def unseal_gt(self, split, gt_file):
        if self.splits.get(split) != "SPLIT_RESOLVED":
            raise ProtocolError("GT barrier requires resolved split")
        path = self._contained(gt_file, "private_synthetic_gt")
        self._replay_integrity()
        data = read_json(path)
        expected = {r["id"] for r in self.attempts.values()
                    if r["split"] == split and r["state"] == "PREDICTIONS_FROZEN"}
        if data.get("split") != split or set(data.get("attempts", {})) != expected:
            raise ProtocolError("Synthetic GT set is incomplete or from wrong split")
        return self._emit("SPLIT", {"split": split, "to": "GT_UNSEALED",
                                    "synthetic_gt_path": str(path.relative_to(self.root))}, [path])

    def admit(self, split, attempt_id, gt_file):
        if self.splits.get(split) != "GT_UNSEALED":
            raise ProtocolError("Only trusted evaluator can admit after GT unseal")
        attempt = self.attempts.get(attempt_id)
        if (not attempt or attempt["split"] != split or
                attempt["state"] != "PREDICTIONS_FROZEN" or
                attempt_id in self.admissions):
            raise ProtocolError("Attempt is not eligible for evaluator admission")
        path = self._contained(gt_file, "private_synthetic_gt")
        if str(path.relative_to(self.root)) != self.gt_paths.get(split):
            raise ProtocolError("Evaluator GT differs from the frozen unseal source")
        self._replay_integrity()
        row = read_json(path)["attempts"][attempt_id]
        layer, entity = row["root_layer"], row["root_entity"]
        impact = row["business_impact"]
        if (layer not in LAYERS or not entity.startswith(layer + "/") or
                impact not in {"STRONG", "WEAK", "NONE", "UNMEASURABLE"}):
            raise ProtocolError("Synthetic evaluator label invalid")
        public = read_json(self._bundle_paths(attempt_id)[2])
        observable = (bool(row["observability_valid"]) and
                      entity in public["candidates"] and
                      bool(row["independent_public_root_evidence"]))
        admitted = bool(row["injection_valid"]) and observable
        decision = {"id": attempt_id, "split": split,
                    "status": "ADMITTED" if admitted else "INVALID_POST_GT",
                    "root_layer": layer, "root_entity": entity,
                    "injection_valid": bool(row["injection_valid"]),
                    "observability_valid": observable,
                    "business_impact": impact,
                    "root_observable": admitted,
                    "user_impactful": admitted and impact in {"STRONG", "WEAK"}}
        self._emit("ADMISSION", decision, [path, *self._bundle_paths(attempt_id)])
        return decision

    def select(self, split):
        if self.splits.get(split) != "GT_UNSEALED":
            raise ProtocolError("Selection requires GT unsealed")
        self._replay_integrity()
        rows = [r for r in self.attempts.values() if r["split"] == split]
        if any(r["state"] == "PREDICTIONS_FROZEN" for r in rows):
            raise ProtocolError("All admission decisions must precede selection")
        primaries = sorted((r for r in rows if r["kind"] == "PRIMARY"),
                           key=lambda r: r["order"])
        reserves = sorted((r for r in rows if r["kind"] == "RESERVE"),
                          key=lambda r: (r["layer"], r["order"]))
        used = set()
        slots = {}
        for primary in primaries:
            chosen = primary if primary["state"] == "ADMITTED" else next(
                (r for r in reserves if r["layer"] == primary["layer"] and
                 r["state"] == "ADMITTED" and r["id"] not in used), None)
            slots[primary["id"]] = chosen["id"] if chosen else None
            if chosen and chosen["kind"] == "RESERVE":
                used.add(chosen["id"])
        self._emit("SELECTION", {"split": split, "slots": slots,
                                  "shortfalls": sum(x is None for x in slots.values())})
        return slots

    def score(self, split):
        if self.splits.get(split) != "SELECTION_FROZEN":
            raise ProtocolError("Scoring requires frozen selection")
        self._replay_integrity()
        result = {}
        for method in self.registry["methods"]:
            for variant in VARIANTS:
                counts = Counter()
                ranks = []
                rows = []
                for slot, attempt_id in self.selection[split].items():
                    if attempt_id is None:
                        rows.append({"slot": slot, "status": "SHORTFALL"})
                        continue
                    gt = self.admissions[attempt_id]
                    public = read_json(self.root / "public" / attempt_id /
                                       (variant + ".json"))
                    layer, entity = gt["root_layer"], gt["root_entity"]
                    if (layer not in self.registry["method_layers"][method] or
                            entity not in public["candidates"] or
                            (self.registry["method_entities"] is not None and
                             entity not in self.registry["method_entities"][method])):
                        rows.append({"slot": slot, "status": "OUT_OF_CANDIDATE_SPACE"})
                        continue
                    run = read_json(self.root / "predictions" / attempt_id /
                                    method / (variant + ".json"))
                    if run["status"] == "METHOD_PREREQUISITE_BLOCKED":
                        rows.append({"slot": slot, "status": run["status"]})
                        continue
                    counts["applicable"] += 1
                    counts["user_impactful_applicable"] += gt["user_impactful"]
                    if run["status"] != "SCORED":
                        rows.append({"slot": slot, "status": run["status"],
                                     "layer": layer,
                                     "mechanism": self.attempts[attempt_id]["mechanism"],
                                     "business_impact": gt["business_impact"]})
                        continue
                    counts["predicted"] += 1
                    ranking = [r["entity"] for r in run["ranking"]]
                    rank = ranking.index(entity) + 1 if entity in ranking else None
                    top1 = ranking[0]
                    layer_correct = top1.split("/", 1)[0] == layer
                    counts["layer_correct"] += layer_correct
                    counts["entity_correct"] += top1 == entity
                    counts["top3_correct"] += rank is not None and rank <= 3
                    counts["conditional_layer_count"] += layer_correct
                    counts["conditional_entity_correct"] += layer_correct and top1 == entity
                    counts["non_network_predicted"] += layer != "NETWORK_PATH"
                    counts["network_false_positive"] += (layer != "NETWORK_PATH" and
                                                          top1.startswith("NETWORK_PATH/"))
                    if rank is not None:
                        ranks.append(rank)
                    rows.append({"slot": slot, "status": "SCORED", "root_rank": rank,
                                 "top1": top1, "exact": top1 == entity,
                                 "layer": layer, "mechanism": self.attempts[attempt_id]["mechanism"],
                                 "business_impact": gt["business_impact"]})
                n, p = counts["applicable"], counts["predicted"]
                strata = {}
                for field in ("layer", "mechanism", "business_impact"):
                    groups = {}
                    for row in rows:
                        if row["status"] not in {"SCORED", "NO_PREDICTION",
                                                 "INFERENCE_FAILURE"}:
                            continue
                        group = groups.setdefault(row[field], {"applicable": 0,
                            "valid_predictions": 0, "exact_correct": 0})
                        group["applicable"] += 1
                        group["valid_predictions"] += row["status"] == "SCORED"
                        group["exact_correct"] += bool(row.get("exact"))
                    for group in groups.values():
                        group["prediction_coverage"] = (group["valid_predictions"] /
                                                        group["applicable"])
                        group["coverage_adjusted_exact_at_1"] = (
                            group["exact_correct"] / group["applicable"])
                    strata[field] = groups
                result[method + "/" + variant] = {
                    "applicable": n, "valid_predictions": p,
                    "status_counts": dict(Counter(r["status"] for r in rows)),
                    "prediction_coverage": p/n if n else None,
                    "coverage_adjusted_exact_at_1": counts["entity_correct"]/n if n else None,
                    "exact_at_1_on_predictions": counts["entity_correct"]/p if p else None,
                    "layer_at_1": counts["layer_correct"]/p if p else None,
                    "entity_at_1_given_correct_layer":
                        counts["conditional_entity_correct"]/counts["conditional_layer_count"]
                        if counts["conditional_layer_count"] else None,
                    "top3": counts["top3_correct"]/p if p else None,
                    "mrr": sum(1/r for r in ranks)/p if p else None,
                    "mean_root_rank": sum(ranks)/len(ranks) if ranks else None,
                    "root_rank_coverage": len(ranks)/p if p else None,
                    "network_path_false_positive_rate":
                        counts["network_false_positive"]/counts["non_network_predicted"]
                        if variant != "A" and counts["non_network_predicted"] else None,
                    "strata": strata, "per_slot": rows}
        paired = {}
        applicable_statuses = {"SCORED", "NO_PREDICTION", "INFERENCE_FAILURE"}
        for method in self.registry["methods"]:
            for left, right, non_network_only in (("A", "B1", True),
                                                  ("B1", "B2", False)):
                a = {row["slot"]: row for row in result[method + "/" + left]["per_slot"]}
                b = {row["slot"]: row for row in result[method + "/" + right]["per_slot"]}
                count = Counter()
                for slot, attempt_id in self.selection[split].items():
                    if attempt_id is None:
                        continue
                    layer = self.admissions[attempt_id]["root_layer"]
                    if non_network_only and layer == "NETWORK_PATH":
                        continue
                    left_row, right_row = a[slot], b[slot]
                    if (left_row["status"] not in applicable_statuses or
                            right_row["status"] not in applicable_statuses):
                        continue
                    count["applicable_paired_cases"] += 1
                    count["left_exact_correct"] += bool(left_row.get("exact"))
                    count["right_exact_correct"] += bool(right_row.get("exact"))
                    if (left_row["status"] == "SCORED" and
                            right_row["status"] == "SCORED"):
                        count["both_valid_predictions"] += 1
                        count["left_conditional_exact_correct"] += bool(left_row["exact"])
                        count["right_conditional_exact_correct"] += bool(right_row["exact"])
                n = count["applicable_paired_cases"]
                both = count["both_valid_predictions"]
                paired[method + "/" + left + "_to_" + right] = {
                    "case_scope": "NON_NETWORK_ONLY" if non_network_only else "ALL_LAYERS",
                    "applicable_paired_cases": n,
                    "both_valid_predictions": both,
                    "coverage_adjusted_exact_at_1_left":
                        count["left_exact_correct"]/n if n else None,
                    "coverage_adjusted_exact_at_1_right":
                        count["right_exact_correct"]/n if n else None,
                    "conditional_exact_on_both_predicted_left":
                        count["left_conditional_exact_correct"]/both if both else None,
                    "conditional_exact_on_both_predicted_right":
                        count["right_conditional_exact_correct"]/both if both else None,
                    "network_context_consumed_by_method": None}
        result["paired_comparisons"] = paired
        self._emit("SCORES", {"split": split, "methods": result})
        return result
