from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request, status

from app.access_control import AccessController
from app.questions import QuestionCatalog
from app.storage import CaseStorage, StorageError

from .evidence_verifier import EvidenceVerifier
from .grader_client import PrivateGraderClient
from .models import FAULT_TYPES, SubmissionRequest
from .settings import load_grading_settings
from .store import SubmissionStore
from .worker import GradingWorker


def answer_digest(answer: dict[str, Any]) -> str:
    raw = json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def grading_mode_for(case_id: str) -> str:
    if case_id == "DEMO-001":
        return "DEMO"
    if case_id in {"SF-001", "SF-002"}:
        return "PREVIEW"
    return "OFFICIAL"


def submission_schema_allowed(case_id: str, schema_version: str, task_type: str | None = None) -> bool:
    # Compatibility helper for historical callers/tests. The live submission
    # route always supplies task_type and therefore follows the new branch.
    if task_type is None:
        return case_id not in {"SF-001", "SF-002"} or schema_version == "3.0"
    if task_type == "SEMIFINAL_QUERY_VISUALIZATION":
        return schema_version == "1.0"
    # Historical records remain readable. Practice keeps the v3 anomaly path
    # for final-Agent rehearsal, while the new formal semifinal accepts QA.
    if case_id in {"SF-001", "SF-002"}:
        return task_type == "SEMIFINAL_ANOMALY_DETECTION" and schema_version == "3.0"
    return False


def public_submission(row: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "submission_id": row["submission_id"],
        "case_id": row["case_id"],
        "schema_version": row["schema_version"],
        "status": row["status"],
        "received_at": row["received_at"],
        "updated_at": row["updated_at"],
        "grading_mode": row.get("grading_mode") or ("DEMO" if row["is_demo"] else "OFFICIAL"),
        "mode": row.get("competition_mode") or "LEGACY",
        "session": row.get("session") or "UNASSIGNED",
        "team_id": row["team_id"],
        "task_type": row.get("task_type"),
        "question_id": row.get("question_id"),
        "message": row.get("public_message"),
    }
    if row["status"] in {"COMPLETED", "PREVIEW_COMPLETED"}:
        payload.update(
            {
                "score": row.get("total_score"),
                "breakdown": row.get("breakdown", {}),
                "penalty": row.get("penalty") or 0.0,
                "grader_version": row.get("grader_version"),
                "rubric_version": row.get("rubric_version"),
                "gt_version_identifier": row.get("gt_version_identifier"),
                "llm_model_identifier": row.get("llm_model_identifier"),
                "task_type": row.get("task_type") or ("SEMIFINAL_ANOMALY_DETECTION" if row["schema_version"] == "3.0" else "FINAL_ROOT_CAUSE_ANALYSIS"),
            }
        )
    if row["status"] in {"WAITING_JUDGE", "PENDING_JUDGE"}:
        payload["judge_progress"] = row.get("judge_progress", {})
        payload["judge_failure_code"] = row.get("judge_failure_code")
        payload["judge_attempts"] = int(row.get("judge_attempts") or 0)
    return payload


def install_submission_api(
    app: FastAPI,
    storage: CaseStorage,
    access: AccessController,
    questions: QuestionCatalog,
) -> None:
    settings = load_grading_settings()
    store = SubmissionStore(settings.database_path, settings.submission_dir)
    verifier = EvidenceVerifier(
        storage,
        Path(__file__).resolve().parents[1] / "demo" / "evidence.json",
    )
    grader = PrivateGraderClient(settings)
    worker = GradingWorker(settings, store, verifier, grader)
    router = APIRouter(prefix="/v1", tags=["submissions"])

    @router.get("/fault-taxonomy")
    async def fault_taxonomy():
        return {"version": "1.0", "fault_types": sorted(FAULT_TYPES)}

    @router.get("/anomaly-taxonomy")
    async def anomaly_taxonomy():
        path = Path(__file__).resolve().parents[1] / "schemas" / "anomaly-taxonomy-v1.json"
        return json.loads(path.read_text(encoding="utf-8"))

    @router.post("/{mode}/submissions", status_code=status.HTTP_202_ACCEPTED)
    @router.post("/{mode}/submissions/qa", status_code=status.HTTP_202_ACCEPTED)
    @router.post("/submissions", status_code=status.HTTP_202_ACCEPTED)
    async def create_submission(
        payload: SubmissionRequest,
        request: Request,
        mode: str = "legacy",
    ):
        content_length = int(request.headers.get("content-length", "0") or 0)
        if content_length > max(settings.max_body_bytes, settings.max_qa_body_bytes):
            raise StorageError("SUBMISSION_TOO_LARGE", "submission body exceeds configured limit", 413)
        # Hash the exact JSON answer object received from the client.  Re-serializing
        # the Pydantic model would normalize UTC spellings (Z versus +00:00) and add
        # omitted optional fields, causing valid client hashes to be rejected.
        raw_envelope = await request.json()
        supplied_answer = raw_envelope.get("answer") if isinstance(raw_envelope, dict) else None
        if not isinstance(supplied_answer, dict):
            raise StorageError("INVALID_ANSWER", "answer must be a JSON object", 400)
        task_type = str(supplied_answer.get("task_type", ""))
        if request.url.path.endswith("/submissions/qa") and task_type != "SEMIFINAL_QUERY_VISUALIZATION":
            raise StorageError("TASK_TYPE_MISMATCH", "the QA submission endpoint only accepts query/visualization answers", 400)
        body_limit = settings.max_qa_body_bytes if task_type == "SEMIFINAL_QUERY_VISUALIZATION" else settings.max_body_bytes
        if content_length > body_limit:
            raise StorageError("SUBMISSION_TOO_LARGE", "submission body exceeds configured task limit", 413)
        if answer_digest(supplied_answer) != payload.answer_sha256:
            raise StorageError("ANSWER_HASH_MISMATCH", "answer_sha256 does not match the submitted answer", 400)
        if not submission_schema_allowed(payload.case_id, payload.schema_version, task_type):
            raise StorageError(
                "SCHEMA_VERSION_NOT_ALLOWED",
                "schema_version and task_type are not enabled for this competition mode",
                400,
            )
        effective_mode = grading_mode_for(payload.case_id)
        route_mode = "practice" if effective_mode in {"DEMO", "PREVIEW"} else "semifinal"
        if mode != "legacy" and mode != route_mode:
            raise StorageError("CASE_NOT_FOUND", "case is not published or not authorized", 404)
        team = access.authorize(request, route_mode, payload.case_id)
        if getattr(request.state, "am_preview_test", False) and task_type != "SEMIFINAL_QUERY_VISUALIZATION":
            raise StorageError("CASE_NOT_FOUND", "case is not published or not authorized", 404)
        question_id = supplied_answer.get("question_id")
        question: dict[str, Any] | None = None
        if task_type == "SEMIFINAL_QUERY_VISUALIZATION":
            question = questions.get_visible(
                route_mode,
                str(question_id),
                access.authorized_cases(request, route_mode),
                include_preview=bool(getattr(request.state, "internal_preview", False)),
            )
            if question.get("case_id") != payload.case_id:
                raise StorageError("QUESTION_CASE_MISMATCH", "question does not belong to the submitted case", 400)
        existing = store.by_idempotency(team.team_id, payload.idempotency_key)
        if existing:
            if existing["answer_sha256"] != payload.answer_sha256:
                raise StorageError("IDEMPOTENCY_CONFLICT", "idempotency key was already used for another answer", 409)
            return public_submission(existing)
        is_demo = payload.case_id == "DEMO-001"
        # SF-001/002 are practice cases regardless of historical answer
        # schema.  Never charge a new practice submission to the formal quota.
        grading_mode = (
            "PREVIEW"
            if question is not None and question.get("publication_status") == "SEMIFINAL_PREVIEW"
            else effective_mode
        )
        try:
            verifier.case_window(payload.case_id)
        except StorageError:
            raise
        # QA is an iterative query/plot exercise: unlimited attempts are
        # authorized. Preserve the existing quota for other task types.
        if (grading_mode == "OFFICIAL" and task_type != "SEMIFINAL_QUERY_VISUALIZATION"
                and store.official_count(team.team_id, payload.case_id) >= settings.max_official_submissions):
            raise StorageError("SUBMISSION_LIMIT_REACHED", "formal submission limit reached for this case", 429)
        submission_id = "SUB-" + uuid.uuid4().hex[:20].upper()
        store.create(
            {
                "submission_id": submission_id,
                "team_id": team.team_id,
                "case_id": payload.case_id,
                "schema_version": payload.schema_version,
                "idempotency_key": payload.idempotency_key,
                "answer_sha256": payload.answer_sha256,
                "status": "RECEIVED",
                "is_demo": is_demo,
                "grading_mode": grading_mode,
                "competition_mode": route_mode.upper(),
                "session": team.session,
                "token_id": team.token_id,
                "task_type": task_type,
                "question_id": question_id,
            },
            supplied_answer,
        )
        await worker.enqueue(submission_id)
        return public_submission(store.get(submission_id, team.team_id))

    @router.get("/{mode}/scores/semifinal")
    async def semifinal_score_summary(request: Request, mode: str):
        if mode not in {"practice", "semifinal"}:
            raise StorageError("MODE_NOT_FOUND", "competition mode does not exist", 404)
        team = access.authorize(request, mode)
        if mode == "semifinal" and not getattr(team, "is_organizer_test", False):
            try:
                plan_path = Path(os.environ.get("AIOPS_SEMIFINAL_SCORING_PLAN", "/etc/aiops-contest/semifinal-scoring-plan.json"))
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                if not isinstance(plan, dict) or plan.get("status") != "APPROVED_FOR_SCORING":
                    raise ValueError("unapproved plan")
                summary = store.qa_summary(team.team_id, mode, scoring_plan=plan)
            except (OSError, ValueError, KeyError, TypeError, StopIteration):
                raise StorageError("SCORING_PLAN_UNAVAILABLE", "official semifinal scoring plan is unavailable", 503)
        else:
            summary = store.qa_summary(team.team_id, mode)
        summary.update({"team_id": team.team_id, "mode": mode.upper()})
        return summary

    @router.get("/{mode}/submissions/{submission_id}")
    @router.get("/submissions/{submission_id}")
    async def get_submission(
        submission_id: str,
        request: Request,
        mode: str = "legacy",
    ):
        if not submission_id.startswith("SUB-") or len(submission_id) > 64:
            raise StorageError("SUBMISSION_NOT_FOUND", "submission does not exist", 404)
        team = access.authenticate(request)
        row = store.get(submission_id, team.team_id)
        if not row:
            raise StorageError("SUBMISSION_NOT_FOUND", "submission does not exist", 404)
        row_mode = str(row.get("competition_mode") or "LEGACY").lower()
        if row_mode == "legacy":
            row_mode = "practice" if grading_mode_for(row["case_id"]) in {"DEMO", "PREVIEW"} else "semifinal"
        if mode != "legacy" and mode != row_mode:
            raise StorageError("SUBMISSION_NOT_FOUND", "submission does not exist", 404)
        access.authorize(request, row_mode, row["case_id"])
        return public_submission(row)

    @app.on_event("startup")
    async def start_grading_worker():
        await worker.start()

    @app.on_event("shutdown")
    async def stop_grading_worker():
        await worker.stop()

    app.include_router(router)
    app.state.grading_store = store
    app.state.grading_worker = worker
