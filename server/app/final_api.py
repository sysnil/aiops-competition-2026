from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request

from grading.submission_api.evidence_verifier import EvidenceVerifier
from grading.submission_api.final_models import FinalSubmission
from grading.submission_api.grader_client import PrivateGraderClient
from grading.submission_api.settings import load_grading_settings

from .access_control import AccessController
from .final_store import FinalStore, final_display_score, parse_time, final_window_visible
from .security import FairLimiter, RateLimitError, TeamIdentity
from .storage import CaseStorage, StorageError


ORGANIZER_PREVIEW_SUBMISSION_LIMIT = 20


def preview_submission_limit(team: TeamIdentity, *, preview: bool, scoped: bool,
                             scoped_limit: int | None) -> int | None:
    if preview and team.is_organizer_test and not scoped:
        return ORGANIZER_PREVIEW_SUBMISSION_LIMIT
    return scoped_limit if scoped else None


def _digest(answer: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _visible(row: dict[str, Any], now: datetime) -> bool:
    return final_window_visible(row, now)


def visible_grade(grade: dict[str, Any] | None, *, show_details: bool) -> dict[str, Any] | None:
    if not isinstance(grade, dict):
        return None
    if show_details:
        result = {key: grade.get(key) for key in ("status", "score", "breakdown", "grader_version", "rubric_version", "review_status", "grading_mode")}
        score = result.get("score")
        if (result.get("status") in {"COMPLETED", "PREVIEW_COMPLETED"}
                and isinstance(score, (int, float)) and not isinstance(score, bool)
                and math.isfinite(score) and 0 <= score <= 10):
            result["display_score"] = final_display_score(float(score))
            result["display_max_score"] = 100
        if grade.get("grading_mode") == "HYBRID_INTERNAL_PREVIEW" and isinstance(result.get("breakdown"), dict):
            result["breakdown"] = {key: value for key, value in result["breakdown"].items() if key != "gt_events"}
        return result
    score = grade.get("score")
    if (grade.get("status") in {"COMPLETED", "PREVIEW_COMPLETED"}
            and isinstance(score, (int, float)) and not isinstance(score, bool)
            and math.isfinite(score) and 0 <= score <= 10):
        return {"status": grade["status"], "score": score,
                "display_score": final_display_score(float(score)), "display_max_score": 100}
    return None


def visible_round_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result or result.get("rule") != "ACCURACY_THEN_SERVER_RECEIPT_V1":
        return result
    public = dict(result)
    public["display_totals"] = {
        team: final_display_score(float(score)) for team, score in result["totals"].items()
    }
    public["display_max_score"] = 100
    public["entries"] = {
        team: {**entry, "display_score": final_display_score(float(entry["score"]))}
        for team, entry in result["entries"].items()
    }
    return public


def install_final_api(app: FastAPI, storage: CaseStorage, access: AccessController, limiter: FairLimiter) -> FinalStore:
    settings = load_grading_settings()
    catalog_path = Path(os.environ.get("AIOPS_FINAL_TASK_CATALOG", Path(__file__).resolve().parents[1] / "config" / "final-stage1-catalog.json"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    internal_batch_id = os.environ.get("AIOPS_FINAL_INTERNAL_BATCH_ID", "").strip()
    internal_grading_mode = os.environ.get("AIOPS_FINAL_INTERNAL_GRADING_MODE", "INTERNAL_PREVIEW")
    if internal_batch_id and internal_grading_mode not in {"INTERNAL_PREVIEW", "HYBRID_INTERNAL_PREVIEW"}:
        raise ValueError("unsupported internal Final grading mode")
    final = FinalStore(settings.database_path, catalog_path)
    verifier = EvidenceVerifier(storage, Path(__file__).resolve().parents[1] / "grading" / "demo" / "evidence.json")
    grader = PrivateGraderClient(settings)
    router = APIRouter(prefix="/v1/final", tags=["final-stage1"])
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
    workers: list[asyncio.Task] = []

    async def grade_one(submission_id: str) -> None:
        if not final.claim_for_grading(submission_id):
            return
        row = final.submission(submission_id)
        answer = json.loads(row["answer_json"])
        validation = await verifier.validate_answer(answer)
        if not validation["valid"]:
            result = {"status": "REJECTED", "grading_mode": row["grading_mode"], "review_status": "INVALID_PUBLIC_EVIDENCE", "invalid_evidence_codes": sorted({item.get("code") for item in validation["checks"] if not item.get("valid")})}
        else:
            result = await grader.grade({
                "submission_id": submission_id, "case_id": row["case_id"],
                "answer": answer, "evidence_validation": validation,
                "grading_mode": row["grading_mode"],
            })
        final.set_grade(submission_id, result)

    async def run_worker() -> None:
        while True:
            submission_id = await queue.get()
            try:
                await grade_one(submission_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                final.mark_pending(submission_id, type(exc).__name__)
            finally:
                queue.task_done()

    @app.on_event("startup")
    async def start_final_worker():
        if not workers:
            final.recover_interrupted_grading()
            workers.extend(asyncio.create_task(run_worker(), name=f"final-worker-{index}") for index in range(2))
            for submission_id in final.pending_ids():
                await queue.put(submission_id)

    @app.on_event("shutdown")
    async def stop_final_worker():
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        workers.clear()

    def identity(request: Request) -> tuple[TeamIdentity, bool]:
        team = access.authenticate(request)
        source = str(getattr(request.state, "source_ip", ""))
        if team.assigned_ip and not hmac.compare_digest(source, team.assigned_ip):
            raise StorageError("TASK_NOT_FOUND", "task does not exist or is not available", 404)
        organizer_preview = bool(team.is_organizer_test and team.assigned_ip and source == team.assigned_ip)
        grant = access.state.read().get("final", {}).get("team_preview_test", {})
        try:
            active = parse_time(grant.get("expires_at_utc")) > datetime.now(timezone.utc)
        except (TypeError, ValueError):
            active = False
        scoped_preview = bool(
            grant.get("enabled") is True and active and not organizer_preview
            and "FINAL" not in team.allowed_modes
            and team.team_id == grant.get("team_id")
            and source == team.assigned_ip == grant.get("assigned_ip")
            and isinstance(grant.get("task_ids"), list) and bool(grant["task_ids"])
        )
        request.state.final_scoped_preview_tasks = frozenset(grant["task_ids"]) if scoped_preview else frozenset()
        preview = organizer_preview or scoped_preview
        if not preview and "FINAL" not in team.allowed_modes:
            raise StorageError("TASK_NOT_FOUND", "task does not exist or is not available", 404)
        request.state.competition_mode = "FINAL_PREVIEW" if preview else "FINAL"
        return team, preview

    async def rate_guard(request: Request) -> None:
        team, _ = identity(request)
        try:
            await limiter.check_rate(f"{team.team_id}:final-stage1")
        except RateLimitError as exc:
            raise StorageError("RATE_LIMITED", "team request rate exceeded", 429) from exc

    def allowed_task(request: Request, task_id: str) -> tuple[TeamIdentity, dict[str, Any], bool]:
        team, preview = identity(request)
        task = final.task(task_id)
        if not task["case_id"]:
            raise StorageError("TASK_NOT_FOUND", "task does not exist or is not available", 404)
        scoped_tasks = request.state.final_scoped_preview_tasks
        if scoped_tasks and task_id not in scoped_tasks:
            raise StorageError("TASK_NOT_FOUND", "task does not exist or is not available", 404)
        if task["kind"] == "FT":
            allowed = _visible(task, datetime.now(timezone.utc))
        else:
            round_item = final.round(task_id)
            allowed = _visible(round_item, datetime.now(timezone.utc)) and team.team_id in {round_item["champion_team_id"], round_item["challenger_team_id"]}
        if not allowed and not preview:
            raise StorageError("TASK_NOT_FOUND", "task does not exist or is not available", 404)
        request.state.case_id = task["case_id"]
        return team, task, preview

    def public_task(task: dict[str, Any], *, scoped_preview: bool = False) -> dict[str, Any]:
        manifest = storage.load_manifest(task["case_id"], mock_only=False)
        display = next((item for item in catalog["tasks"] if item["task_id"] == task["task_id"]), {})
        status = task["status"]
        opens_at = task["opens_at"]
        closes_at = task["closes_at"]
        if task["kind"] == "PK":
            round_item = final.round(task["task_id"])
            status = round_item["status"]
            opens_at = round_item["opens_at"]
            closes_at = round_item["closes_at"]
        row_counts = manifest.get("source_row_counts") or {}
        if not isinstance(row_counts, dict):
            row_counts = {}
        public_record_counts = {
            key: row_counts[key] for key in ("metrics", "logs", "traces")
            if type(row_counts.get(key)) is int and row_counts[key] >= 0
        }
        unique_traces = manifest.get("source_unique_trace_count")
        if type(unique_traces) is int and unique_traces >= 0:
            public_record_counts["trace_ids"] = unique_traces
        return {
            "task_id": task["task_id"], "case_id": task["case_id"],
            "kind": task["kind"], "status": "OPEN" if scoped_preview and task["kind"] == "FT" else status,
            "internal_test_preview": scoped_preview,
            "title": display.get("title") or task["task_id"],
            "description": display.get("description") or catalog["public_prompt"],
            "opens_at": opens_at, "closes_at": closes_at,
            "query_start_utc": manifest["start_time"], "query_end_utc": manifest["end_time"],
            "modalities": manifest["available_modalities"], "topology_available": True,
            "public_record_counts": public_record_counts,
            "max_submissions": None if task["kind"] == "FT" and task["max_submissions"] == 0 else task["max_submissions"],
            "submission_limit_policy": "UNLIMITED" if task["kind"] == "FT" and task["max_submissions"] == 0 else "LIMITED",
            "answer_schema_version": "final-1.0",
            "prompt": catalog["public_prompt"],
            "internal_preview_batch": internal_batch_id or None,
        }

    @router.get("/tasks")
    async def list_tasks(request: Request):
        team, preview = identity(request)
        items = []
        for task in final.tasks():
            try:
                _, _, allowed_preview = allowed_task(request, task["task_id"])
                if allowed_preview == preview:
                    items.append(public_task(task, scoped_preview=bool(request.state.final_scoped_preview_tasks)))
            except StorageError:
                continue
        return {"team_id": team.team_id, "internal_preview": preview, "internal_preview_batch": internal_batch_id or None, "items": items}

    @router.get("/tasks/{task_id}")
    async def task_detail(request: Request, task_id: str):
        _, task, preview = allowed_task(request, task_id)
        return {"task": public_task(task, scoped_preview=bool(request.state.final_scoped_preview_tasks)), "internal_preview": preview}

    @router.get("/tasks/{task_id}/case/metadata")
    async def task_metadata(request: Request, task_id: str):
        _, task, _ = allowed_task(request, task_id)
        return storage.public_metadata(storage.load_manifest(task["case_id"], mock_only=False))

    @router.get("/tasks/{task_id}/case/topology")
    async def task_topology(request: Request, task_id: str):
        _, task, _ = allowed_task(request, task_id)
        manifest = storage.load_manifest(task["case_id"], mock_only=False)
        return {"case_id": task["case_id"], "topology": manifest["public_topology"]}

    @router.get("/tasks/{task_id}/case/{modality}")
    async def task_observability(
        request: Request, task_id: str, modality: str,
        start: datetime | None = None, end: datetime | None = None,
        service: str | None = Query(None, max_length=128),
        pod: str | None = Query(None, max_length=128),
        node: str | None = Query(None, max_length=128),
        metric_name: str | None = Query(None, max_length=128),
        severity: str | None = Query(None, max_length=128),
        trace_id: str | None = Query(None, max_length=128),
        keyword: str | None = Query(None, max_length=256),
        status: str | None = Query(None, max_length=128),
        operation_name: str | None = Query(None, max_length=128),
        parent_service: str | None = Query(None, max_length=128),
        page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=1000),
    ):
        _, task, _ = allowed_task(request, task_id)
        manifest = storage.load_manifest(task["case_id"], mock_only=False)
        if (start and start.tzinfo is None) or (end and end.tzinfo is None):
            raise StorageError("TIMEZONE_REQUIRED", "query times require timezone", 400)
        left, right = storage.validate_window(manifest, start, end)
        limit, offset = storage.validate_page(page, page_size)
        if modality == "metrics":
            rows = await storage.query_metrics(manifest, start=left, end=right, metric_name=metric_name, service=service, pod=pod, node=node, aggregation="raw", step_seconds=60, limit=limit, offset=offset)
        elif modality == "logs":
            rows = await storage.query_logs(manifest, start=left, end=right, service=service, pod=pod, severity=severity, trace_id=trace_id, keyword=keyword, limit=limit, offset=offset)
        elif modality == "traces":
            rows = await storage.query_traces(manifest, start=left, end=right, service=service, status=status, operation_name=operation_name, parent_service=parent_service, limit=limit, offset=offset)
        else:
            raise StorageError("MODALITY_NOT_FOUND", "modality does not exist", 404)
        allowed_task(request, task_id)
        return {"case_id": task["case_id"], "page": page, "page_size": page_size, "has_more": len(rows) == page_size, "items": rows}

    @router.get("/tasks/{task_id}/case/traces/{trace_id}")
    async def task_trace(request: Request, task_id: str, trace_id: str):
        _, task, _ = allowed_task(request, task_id)
        manifest = storage.load_manifest(task["case_id"], mock_only=False)
        spans = await storage.get_trace(manifest, trace_id)
        allowed_task(request, task_id)
        return {"case_id": task["case_id"], "trace_id": trace_id, "spans": spans}

    def public_submission(row: dict[str, Any], *, show_grade: bool) -> dict[str, Any]:
        result = {key: row[key] for key in ("submission_id", "task_id", "team_id", "case_id", "received_at", "status")}
        grade = json.loads(row["grade_json"]) if row.get("grade_json") else None
        public = visible_grade(grade, show_details=show_grade)
        if public is not None:
            result["grade"] = public
        return result

    async def submit(request: Request, task_id: str, payload: FinalSubmission):
        team, task, preview = allowed_task(request, task_id)
        if payload.answer.task_id != task_id or payload.answer.case_id != task["case_id"]:
            raise StorageError("TASK_ANSWER_MISMATCH", "answer does not belong to task", 400)
        raw = await request.json()
        if not isinstance(raw, dict) or not isinstance(raw.get("answer"), dict):
            raise StorageError("INVALID_ANSWER", "answer must be an object", 400)
        if _digest(raw["answer"]) != payload.answer_sha256:
            raise StorageError("ANSWER_HASH_MISMATCH", "answer digest mismatch", 400)
        if len(json.dumps(raw).encode()) > 3_000_000:
            raise StorageError("SUBMISSION_TOO_LARGE", "answer exceeds final limit", 413)
        grading_mode = internal_grading_mode if internal_batch_id else ("PREVIEW" if preview else "OFFICIAL")
        row = final.create_submission(
            task_id,
            team.team_id,
            payload.idempotency_key,
            payload.answer_sha256,
            raw["answer"],
            preview=preview,
            grading_mode=grading_mode,
            preview_submission_limit=preview_submission_limit(
                team, preview=preview, scoped=bool(request.state.final_scoped_preview_tasks),
                scoped_limit=access.state.read().get("final", {}).get("team_preview_test", {}).get("max_preview_submissions"),
            ),
        )
        if row["status"] == "RECEIVED":
            try:
                queue.put_nowait(row["submission_id"])
            except asyncio.QueueFull:
                final.mark_pending(row["submission_id"], "QUEUE_FULL")
        return public_submission(row, show_grade=preview and not request.state.final_scoped_preview_tasks)

    @router.post("/tasks/{task_id}/submissions", status_code=202)
    async def submit_task(request: Request, task_id: str, payload: FinalSubmission):
        return await submit(request, task_id, payload)

    @router.post("/pk/rounds/{round_id}/submissions", status_code=202)
    async def submit_pk(request: Request, round_id: str, payload: FinalSubmission):
        if not round_id.startswith("PK-R"):
            raise StorageError("ROUND_NOT_FOUND", "round does not exist", 404)
        return await submit(request, round_id, payload)

    @router.get("/submissions/{submission_id}")
    async def get_submission(request: Request, submission_id: str):
        team, preview = identity(request)
        row = final.submission(submission_id, team.team_id)
        if request.state.final_scoped_preview_tasks and (
            row["task_id"] not in request.state.final_scoped_preview_tasks or row["grading_mode"] != "PREVIEW"
        ):
            raise StorageError("SUBMISSION_NOT_FOUND", "submission does not exist", 404)
        task = final.task(row["task_id"])
        if not preview and task["kind"] == "PK":
            show_grade = final.round(row["task_id"])["status"] in {"RESULT_CONFIRMED", "CLOSED"}
        else:
            show_grade = (preview and not request.state.final_scoped_preview_tasks) or task["status"] in {"SCORED", "CLOSED"}
        return public_submission(row, show_grade=show_grade)

    @router.get("/pk/rounds/current")
    async def current_round(request: Request):
        team, preview = identity(request)
        if request.state.final_scoped_preview_tasks:
            raise StorageError("ROUND_NOT_FOUND", "no authorized open round", 404)
        for row in final.rounds():
            if row["status"] == "RUNNING" and (preview or team.team_id in {row["champion_team_id"], row["challenger_team_id"]}) and _visible(row, datetime.now(timezone.utc)):
                task = final.task(row["task_id"])
                return {"round_id": row["round_id"], "status": row["status"], "opens_at": row["opens_at"], "closes_at": row["closes_at"], "task": public_task(task)}
        raise StorageError("ROUND_NOT_FOUND", "no authorized open round", 404)

    @router.get("/qualification/summary")
    async def qualification_summary(request: Request, team_id: str | None = None):
        team, preview = identity(request)
        organizer_preview = preview and not request.state.final_scoped_preview_tasks
        selected = team_id if organizer_preview and team_id else team.team_id
        mode = internal_grading_mode if internal_batch_id else ("PREVIEW" if preview else "OFFICIAL")
        result = final.qualification_summary(selected, grading_mode=mode)
        result["grading_mode"] = mode
        result["batch_id"] = internal_batch_id or None
        return result

    @router.get("/pk/rounds/{round_id}")
    async def round_detail(request: Request, round_id: str):
        team, preview = identity(request)
        if request.state.final_scoped_preview_tasks:
            raise StorageError("ROUND_NOT_FOUND", "round does not exist", 404)
        row = final.round(round_id)
        if not preview and (team.team_id not in {row["champion_team_id"], row["challenger_team_id"]} or row["status"] == "NOT_OPEN"):
            raise StorageError("ROUND_NOT_FOUND", "round does not exist", 404)
        result = {key: row[key] for key in ("round_id", "status", "opens_at", "closes_at")}
        if preview or row["status"] in {"RESULT_CONFIRMED", "CLOSED"}:
            result["winner_team_id"] = row["winner_team_id"]
            result["champion_team_id"] = row["champion_team_id"]
            result["challenger_team_id"] = row["challenger_team_id"]
            result["confirmed_result"] = visible_round_result(final.confirmed_result(round_id))
            number = int(round_id[-2:])
            result["next_round_id"] = f"PK-R{number + 1:02d}" if any(t["task_id"] == f"PK-R{number + 1:02d}" for t in catalog["tasks"]) else None
        result["batch_id"] = internal_batch_id or None
        return result

    app.include_router(router, dependencies=[Depends(rate_guard)])
    app.state.final_store = final
    return final
