from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .evidence_verifier import EvidenceVerifier
from .grader_client import GraderUnavailable, PrivateGraderClient
from .settings import GradingSettings
from .store import SubmissionStore


LOGGER = logging.getLogger("aiops-grading-worker")


class GradingWorker:
    def __init__(
        self,
        settings: GradingSettings,
        store: SubmissionStore,
        verifier: EvidenceVerifier,
        grader: PrivateGraderClient,
    ) -> None:
        self.settings = settings
        self.store = store
        self.verifier = verifier
        self.grader = grader
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self.tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        if self.tasks:
            return
        self.tasks = [
            asyncio.create_task(self._run(), name=f"grading-worker-{index}")
            for index in range(self.settings.worker_concurrency)
        ]
        for submission_id in self.store.unfinished_ids():
            await self.queue.put(submission_id)

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    async def enqueue(self, submission_id: str) -> None:
        await self.queue.put(submission_id)

    async def _run(self) -> None:
        while True:
            submission_id = await self.queue.get()
            try:
                await self._process(submission_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.exception("grading task failed submission_id=%s", submission_id)
                self.store.update(
                    submission_id,
                    status="FAILED",
                    public_message="评测任务发生内部错误；本次未生成正式成绩。",
                    review_status="SYSTEM_ERROR",
                )
            finally:
                self.queue.task_done()

    async def _process(self, submission_id: str) -> None:
        row = self.store.get(submission_id)
        if not row:
            return
        answer = self.store.answer(submission_id)
        self.store.update(submission_id, status="VALIDATING")
        validation = await self.verifier.validate_answer(answer)
        self.store.update(submission_id, validation_json=json.dumps(validation, ensure_ascii=False))
        if not validation["valid"]:
            error_codes = sorted({check.get("code", "INVALID_EVIDENCE") for check in validation["checks"] if not check.get("valid")})
            self.store.update(
                submission_id,
                status="REJECTED",
                public_message="证据真实性校验失败：" + ", ".join(error_codes),
                review_status="REJECTED",
            )
            return
        self.store.update(submission_id, status="GRADING")
        grade_payload = {
            "submission_id": submission_id,
            "case_id": row["case_id"],
            "answer": answer,
            "evidence_validation": validation,
            "grading_mode": row.get("grading_mode") or ("DEMO" if row["is_demo"] else "OFFICIAL"),
        }
        attempt = int(row.get("judge_attempts") or 0)
        max_attempts = self.settings.judge_task_retries + 1
        while True:
            try:
                result = await self.grader.grade(grade_payload)
            except GraderUnavailable:
                result = {
                    "status": "PENDING_JUDGE",
                    "judge_failure_codes": ["PRIVATE_GRADER_UNAVAILABLE"],
                    "judge_progress": {},
                }
            if result.get("status") != "PENDING_JUDGE":
                break
            attempt += 1
            failure_codes = sorted(set(result.get("judge_failure_codes") or ["JUDGE_UNAVAILABLE"]))
            progress = result.get("judge_progress") or {}
            exhausted = attempt >= max_attempts
            self.store.update(
                submission_id,
                status="PENDING_JUDGE" if exhausted else "WAITING_JUDGE",
                judge_attempts=attempt,
                judge_progress_json=json.dumps(progress, ensure_ascii=False),
                judge_failure_code=",".join(failure_codes),
                grader_version=result.get("grader_version"),
                rubric_version=result.get("rubric_version"),
                gt_version_identifier=result.get("gt_version_identifier"),
                llm_model_identifier=result.get("llm_model_identifier"),
                public_message=(
                    "证据评测部分事件失败且自动重试已用尽；成功事件已持久化，本次没有正式成绩。"
                    if exhausted else
                    "证据评测部分事件正在等待有界重试；已成功事件不会重复调用模型。"
                ),
                review_status="PENDING",
            )
            if exhausted:
                return
            await asyncio.sleep(self.settings.judge_retry_backoff_seconds * attempt)
            self.store.update(submission_id, status="GRADING")
        status = result.get("status")
        if status == "PENDING_GT_APPROVAL":
            self.store.update(
                submission_id,
                status=status,
                public_message="本题尚无人工审核通过的正式 GT；本次未生成正式成绩。",
                review_status="PENDING",
            )
            return
        if status == "PENDING_REVIEW":
            self.store.update(
                submission_id,
                status=status,
                grader_version=result.get("grader_version"),
                rubric_version=result.get("rubric_version"),
                gt_version_identifier=result.get("gt_version_identifier"),
                llm_model_identifier=result.get("llm_model_identifier"),
                public_message="证据评测尚未完成；本次没有正式成绩。",
                review_status="PENDING",
            )
            return
        if status not in {"COMPLETED", "PREVIEW_COMPLETED"}:
            raise ValueError("private grader returned an unsupported status")
        public_breakdown = result.get("breakdown", {})
        self.store.update(
            submission_id,
            status=status,
            grader_version=result.get("grader_version"),
            rubric_version=result.get("rubric_version"),
            gt_version_identifier=result.get("gt_version_identifier"),
            llm_model_identifier=result.get("llm_model_identifier"),
            breakdown_json=json.dumps(public_breakdown, ensure_ascii=False),
            judge_progress_json=json.dumps(result.get("judge_progress") or {}, ensure_ascii=False),
            judge_failure_code=None,
            penalty=float(result.get("penalty", 0.0)),
            total_score=float(result["score"]),
            review_status="FINAL",
            public_message=(
                "合成演示评测完成；该分数不是正式比赛成绩。"
                if row.get("grading_mode") == "DEMO" else
                "正式半决赛题内部预览评测完成；该分数不进入正式排行榜。"
                if row.get("task_type") == "SEMIFINAL_QUERY_VISUALIZATION"
                and row.get("grading_mode") == "PREVIEW"
                and row.get("competition_mode") == "SEMIFINAL" else
                "练习查询绘图题预览评测完成；该分数不进入正式排行榜。"
                if row.get("task_type") == "SEMIFINAL_QUERY_VISUALIZATION" and row.get("grading_mode") == "PREVIEW" else
                "SF-001/002 草案 GT 预评测完成；该分数不是正式比赛成绩，不进入排行榜。"
                if row.get("grading_mode") == "PREVIEW" else "正式评测完成。"
            ),
        )
