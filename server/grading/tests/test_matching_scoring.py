from __future__ import annotations

import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from grading.private_grader.judge_store import JudgeResultStore
from grading.private_grader.llm_judge import JudgeUnavailable
from grading.private_grader.matching import deduplicate_events, maximum_weight_matching
from grading.private_grader.scoring import score_answer


def event(**overrides):
    value = {
        "anomaly_type": "REQUEST_LATENCY_INCREASE",
        "anomaly_description": "service-a request latency is materially higher than its baseline.",
        "entity_type": "Service",
        "entity_id": "service-a",
        "start_time": "2030-01-01T00:10:00+00:00",
        "end_time": "2030-01-01T00:12:00+00:00",
        "evidence": [{"modality": "logs", "record_sha256": "a" * 64, "record": {"body": "x"}}],
    }
    value.update(overrides)
    return value


def gt_event(identifier="GT-1", **overrides):
    value = {
        "gt_event_id": identifier,
        "anomaly_type": "REQUEST_LATENCY_INCREASE",
        "accepted_anomaly_types": [],
        "entity_type": "Service",
        "entity_id": "service-a",
        "accepted_entity_aliases": [],
        "observable_start_time": "2030-01-01T00:10:00+00:00",
        "observable_end_time": "2030-01-01T00:12:00+00:00",
        "evidence_rubric": "test rubric",
        "review_status": "APPROVED",
        "gt_version": "test-1",
        "rubric_version": "rubric-1",
    }
    value.update(overrides)
    return value


def gt(events):
    return {
        "task_type": "SEMIFINAL_ANOMALY_DETECTION",
        "review_status": "APPROVED",
        "gt_version": "test-1",
        "rubric_version": "rubric-1",
        "matching_rule_version": "entity-time-matching-2.0",
        "time_tolerance_seconds": 120,
        "events": events,
    }


def answer(events):
    return {"case_id": "TEST-001", "schema_version": "3.0", "task_type": "SEMIFINAL_ANOMALY_DETECTION", "anomalies": events}


class FakeJudge:
    def __init__(self, grade="FULL"):
        self.grade = grade
        self.config = SimpleNamespace(model="fake-model", prompt_version="test-prompt-1")

    async def evaluate(self, *_):
        ratio = {"FULL": 1.0, "PARTIAL": 0.5, "NONE": 0.0}[self.grade]
        return {"evidence_grade": self.grade, "evidence_score_ratio": ratio, "reason": "synthetic test", "uncertainty": "LOW", "requires_human_review": False}


def validation(count):
    return {"valid": True, "checks": [{"event": i, "valid": True, "verified_record": {"body": "verified"}} for i in range(count)]}


class ScoringTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_correct_scores_100(self):
        result = await score_answer(answer([event()]), gt([gt_event()]), validation(1), FakeJudge())
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual(100.0, result["score"])
        self.assertEqual(40.0, result["breakdown"]["anomaly_detection"])

    async def test_one_thirteen_and_sixteen_events_can_each_score_100(self):
        for count in (1, 13, 16):
            submissions = [event(entity_id=f"service-{index}") for index in range(count)]
            targets = [gt_event(f"GT-{index}", entity_id=f"service-{index}") for index in range(count)]
            result = await score_answer(answer(submissions), gt(targets), validation(count), FakeJudge())
            self.assertEqual(100.0, result["score"], f"count={count}")

    async def test_wrong_anomaly_keeps_entity_time_and_evidence_credit(self):
        result = await score_answer(answer([event(anomaly_type="LOG_ERROR_BURST")]), gt([gt_event()]), validation(1), FakeJudge())
        self.assertEqual(0.0, result["breakdown"]["anomaly_detection"])
        self.assertEqual(60.0, result["score"])

    async def test_successful_event_is_cached_while_failed_event_is_retried(self):
        class FlakyJudge(FakeJudge):
            def __init__(self):
                super().__init__()
                self.calls = {"service-a": 0, "service-b": 0}

            async def evaluate(self, submitted, *_):
                entity = submitted["entity_id"]
                self.calls[entity] += 1
                if entity == "service-b" and self.calls[entity] == 1:
                    raise JudgeUnavailable("synthetic timeout", "JUDGE_TIMEOUT")
                return await super().evaluate(submitted)

        submissions = [event(entity_id="service-a"), event(entity_id="service-b")]
        targets = [gt_event("A", entity_id="service-a"), gt_event("B", entity_id="service-b")]
        judge = FlakyJudge()
        with tempfile.TemporaryDirectory() as directory:
            store = JudgeResultStore(Path(directory) / "judge.sqlite3")
            first = await score_answer(
                answer(submissions), gt(targets), validation(2), judge,
                submission_id="SUB-IDEMPOTENT", judge_store=store,
            )
            self.assertEqual("PENDING_JUDGE", first["status"])
            self.assertEqual({"total": 2, "completed": 1, "failed": 1, "cached": 0}, first["judge_progress"])
            second = await score_answer(
                answer(submissions), gt(targets), validation(2), judge,
                submission_id="SUB-IDEMPOTENT", judge_store=store,
            )
            self.assertEqual("COMPLETED", second["status"])
            self.assertEqual(100.0, second["score"])
            self.assertEqual(1, second["judge_progress"]["cached"])
            self.assertEqual({"service-a": 1, "service-b": 2}, judge.calls)

    async def test_multiple_simulated_teams_use_bounded_concurrency(self):
        class SlowJudge(FakeJudge):
            def __init__(self):
                super().__init__()
                self.active = 0
                self.maximum = 0

            async def evaluate(self, *_):
                self.active += 1
                self.maximum = max(self.maximum, self.active)
                await asyncio.sleep(0.01)
                self.active -= 1
                return await super().evaluate()

        count = 4
        judge = SlowJudge()
        submissions = [event(entity_id=f"service-{index}") for index in range(count)]
        targets = [gt_event(f"GT-{index}", entity_id=f"service-{index}") for index in range(count)]
        semaphore = asyncio.Semaphore(2)
        results = await asyncio.gather(*(
            score_answer(
                answer(copy.deepcopy(submissions)), gt(copy.deepcopy(targets)), validation(count), judge,
                submission_id=f"SIMULATED-TEAM-{team}", judge_semaphore=semaphore,
            )
            for team in range(3)
        ))
        self.assertTrue(all(result["score"] == 100.0 for result in results))
        self.assertEqual(2, judge.maximum)

    async def test_upper_entity_scores_allowed_service_partial(self):
        target = gt_event(entity_type="Pod", entity_id="pod-a", allow_service_level_partial=True, service_id="service-a")
        result = await score_answer(answer([event()]), gt([target]), validation(1), FakeJudge())
        self.assertEqual(10.0, result["breakdown"]["entity"])
        self.assertEqual(90.0, result["score"])

    async def test_one_time_boundary_scores_half_time(self):
        result = await score_answer(answer([event(end_time="2030-01-01T00:20:00+00:00")]), gt([gt_event()]), validation(1), FakeJudge())
        self.assertEqual(10.0, result["breakdown"]["time"])

    async def test_partial_evidence_scores_ten(self):
        result = await score_answer(answer([event()]), gt([gt_event()]), validation(1), FakeJudge("PARTIAL"))
        self.assertEqual(10.0, result["breakdown"]["evidence"])
        self.assertEqual(90.0, result["score"])

    async def test_missing_half_gt_is_normalized(self):
        result = await score_answer(answer([event()]), gt([gt_event(), gt_event("GT-2", entity_id="service-b")]), validation(1), FakeJudge())
        self.assertEqual(50.0, result["score"])

    async def test_extra_event_penalty(self):
        events = [event(), event(entity_id="other", start_time="2030-01-01T00:10:30+00:00")]
        result = await score_answer(answer(events), gt([gt_event()]), validation(2), FakeJudge())
        self.assertEqual(5.0, result["penalty"])
        self.assertEqual(95.0, result["score"])

    async def test_duplicate_not_penalized(self):
        result = await score_answer(answer([event(), copy.deepcopy(event())]), gt([gt_event()]), validation(2), FakeJudge())
        self.assertEqual(1, result["duplicate_count"])
        self.assertEqual(100.0, result["score"])

    def test_same_entity_near_time_different_anomalies_are_not_deduplicated(self):
        events = [event(), event(anomaly_type="LOG_ERROR_BURST")]
        unique, duplicates = deduplicate_events(events)
        self.assertEqual(2, len(unique))
        self.assertEqual([], duplicates)
        targets = [gt_event("LAT"), gt_event("LOG", anomaly_type="LOG_ERROR_BURST")]
        result = maximum_weight_matching(events, targets)
        pairs = {(events[s]["anomaly_type"], targets[g]["anomaly_type"]) for s, g in result.pairs}
        self.assertEqual({("REQUEST_LATENCY_INCREASE", "REQUEST_LATENCY_INCREASE"), ("LOG_ERROR_BURST", "LOG_ERROR_BURST")}, pairs)

    def test_entity_normalization(self):
        result = maximum_weight_matching([event(entity_id=" Service-A ")], [gt_event(entity_type="SERVICE", entity_id="service-a")])
        self.assertEqual([(0, 0)], result.pairs)

    def test_order_does_not_change_matching(self):
        submissions = [event(entity_id="service-a"), event(entity_id="service-b")]
        targets = [gt_event("A"), gt_event("B", entity_id="service-b")]
        first = maximum_weight_matching(submissions, targets)
        reversed_submissions = list(reversed(submissions))
        second = maximum_weight_matching(reversed_submissions, targets)
        first_entities = {(submissions[s]["entity_id"], targets[g]["entity_id"]) for s, g in first.pairs}
        second_entities = {(reversed_submissions[s]["entity_id"], targets[g]["entity_id"]) for s, g in second.pairs}
        self.assertEqual(first_entities, second_entities)

    def test_one_submission_cannot_match_two_gt(self):
        result = maximum_weight_matching([event()], [gt_event("A"), gt_event("B")])
        self.assertEqual(1, len(result.pairs))
        self.assertEqual(1, len(result.unmatched_gt_indices))

    def test_thirteen_gt_events_are_supported(self):
        targets = [gt_event(f"GT-{index}", entity_id=f"service-{index}") for index in range(13)]
        result = maximum_weight_matching([event(entity_id="service-7")], targets)
        self.assertEqual(1, len(result.pairs))
        self.assertEqual(12, len(result.unmatched_gt_indices))


if __name__ == "__main__":
    unittest.main()
