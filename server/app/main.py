from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from grading.submission_api.router import install_submission_api

from .access_control import AccessController
from .final_rounds import FinalRoundController
from .final_api import install_final_api
from .questions import QuestionCatalog
from .security import ConcurrencyLimitError, FairLimiter, RateLimitError, TeamIdentity
from .settings import load_settings
from .storage import CaseStorage, StorageError


logging.basicConfig(level=logging.INFO, format="%(message)s")
LOGGER = logging.getLogger("aiops-contest-api")
SETTINGS = load_settings()
LIMITER = FairLimiter(SETTINGS.requests_per_minute, SETTINGS.per_team_concurrency)
STORAGE = CaseStorage(SETTINGS)
ACCESS = AccessController(SETTINGS.team_registry, SETTINGS.competition_state)
QUESTIONS = QuestionCatalog(Path(os.environ.get(
    "AIOPS_QUESTION_CATALOG",
    Path(__file__).resolve().parents[1] / "config" / "questions.json",
)))
FINAL_ROUNDS = FinalRoundController(
    Path(os.environ.get("AIOPS_FINAL_ROUNDS", "/etc/aiops-contest/final_rounds.json")),
    ACCESS,
)

app = FastAPI(
    title="AIOps Competition Data and Submission API",
    version="2.0.0",
    description="Token-authenticated practice and semifinal observability APIs.",
)
install_final_api(app, STORAGE, ACCESS, LIMITER)
install_submission_api(app, STORAGE, ACCESS, QUESTIONS)


def error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message, "request_id": request_id}})


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = request.headers.get("x-request-id", str(uuid.uuid4()))[:128]
    request.state.team_id = None
    request.state.token_id = None
    request.state.competition_mode = None
    request.state.case_id = None
    request.state.assigned_ip = None
    forwarded = request.headers.get("x-real-ip", "")
    request.state.source_ip = forwarded or (request.client.host if request.client else "unknown")
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        LOGGER.exception(json.dumps({"event": "unhandled_request_error", "request_id": request.state.request_id, "method": request.method, "path": request.url.path}))
        response = error_response(request, 500, "INTERNAL_ERROR", "internal server error")
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    LOGGER.info(json.dumps({
        "event": "request", "request_id": request.state.request_id,
        "team_id": request.state.team_id, "token_id": request.state.token_id,
        "mode": request.state.competition_mode, "case_id": request.state.case_id,
        "source_ip": request.state.source_ip,
        "assigned_ip_match": request.state.assigned_ip == request.state.source_ip if request.state.assigned_ip else None,
        "method": request.method, "path": request.url.path,
        "status": response.status_code, "duration_ms": duration_ms,
    }, separators=(",", ":")))
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(StorageError)
async def storage_error(request: Request, exc: StorageError):
    return error_response(request, exc.status_code, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    LOGGER.warning(json.dumps({"event": "request_validation_failed", "request_id": getattr(request.state, "request_id", "unknown"), "errors": [{"location": [str(item) for item in error.get("loc", ())], "type": error.get("type"), "message": error.get("msg")} for error in exc.errors()]}, separators=(",", ":")))
    return error_response(request, 422, "INVALID_REQUEST", "request parameters failed validation")


async def authorized(request: Request, mode: str, case_id: str | None = None) -> TeamIdentity:
    identity = ACCESS.authorize(request, mode, case_id)
    try:
        await LIMITER.check_rate(f"{identity.team_id}:{mode}")
    except RateLimitError as exc:
        raise StorageError("RATE_LIMITED", "team request rate exceeded", 429) from exc
    return identity


async def authorized_final(request: Request, round_id: str) -> tuple[TeamIdentity, dict[str, Any]]:
    identity, round_config = FINAL_ROUNDS.authorize(request, round_id)
    try:
        await LIMITER.check_rate(f"{identity.team_id}:final:{round_id}")
    except RateLimitError as exc:
        raise StorageError("RATE_LIMITED", "team request rate exceeded", 429) from exc
    return identity, round_config


def checked_window(manifest: dict[str, Any], start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    if (start and start.tzinfo is None) or (end and end.tzinfo is None):
        raise StorageError("TIMEZONE_REQUIRED", "start and end must include a timezone", 400)
    return STORAGE.validate_window(manifest, start, end)


def page_result(case_id: str, page: int, page_size: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"case_id": case_id, "page": page, "page_size": page_size, "has_more": len(items) == page_size, "items": items}


def normalized_mode(mode: str) -> str:
    return "practice" if mode == "legacy" else mode


ShortText = Annotated[str | None, Query(max_length=128)]


@app.get("/healthz", tags=["system"])
async def healthz():
    return {"status": "ok", "service": "aiops-contest-data-api", "version": app.version}


@app.get("/v1/{mode}/questions", tags=["questions"])
async def list_questions(request: Request, mode: str):
    mode = normalized_mode(mode)
    await authorized(request, mode)
    items = QUESTIONS.list_visible(
        mode,
        ACCESS.authorized_cases(request, mode),
        include_preview=bool(getattr(request.state, "internal_preview", False)),
    )
    return {"items": items, "mode": mode.upper()}


@app.get("/v1/{mode}/questions/{question_id}", tags=["questions"])
async def question_detail(request: Request, question_id: str, mode: str):
    mode = normalized_mode(mode)
    await authorized(request, mode)
    question = QUESTIONS.get_visible(
        mode,
        question_id,
        ACCESS.authorized_cases(request, mode),
        include_preview=bool(getattr(request.state, "internal_preview", False)),
    )
    await authorized(request, mode, str(question["case_id"]))
    return question


@app.get("/v1/final/rounds/current", tags=["final"])
async def current_final_round(request: Request):
    identity, item = FINAL_ROUNDS.current(request)
    request.state.competition_mode = "FINAL"
    request.state.case_id = item.get("case_id")
    return {"team_id": identity.team_id, "round": FINAL_ROUNDS.public(item)}


@app.get("/v1/final/rounds/{round_id}", tags=["final"])
async def final_round_detail(request: Request, round_id: str):
    identity, item = await authorized_final(request, round_id)
    return {"team_id": identity.team_id, "round": FINAL_ROUNDS.public(item)}


@app.get("/v1/final/rounds/{round_id}/case/metadata", tags=["final"])
async def final_metadata(request: Request, round_id: str):
    _, item = await authorized_final(request, round_id)
    manifest = STORAGE.load_manifest(str(item["case_id"]), mock_only=False)
    return STORAGE.public_metadata(manifest)


@app.get("/v1/final/rounds/{round_id}/case/topology", tags=["final"])
async def final_topology(request: Request, round_id: str):
    _, item = await authorized_final(request, round_id)
    manifest = STORAGE.load_manifest(str(item["case_id"]), mock_only=False)
    return {"case_id": item["case_id"], "topology": manifest["public_topology"]}


@app.get("/v1/final/rounds/{round_id}/case/metrics", tags=["final"])
async def final_metrics(request: Request, round_id: str, start: datetime | None = None, end: datetime | None = None, metric_name: ShortText = None, service: ShortText = None, pod: ShortText = None, node: ShortText = None, aggregation: Annotated[str, Query(pattern="^(raw|avg|min|max|sum)$")] = "raw", step_seconds: Annotated[int, Query(ge=10, le=3600)] = 60, page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1)] = 100):
    identity, item = await authorized_final(request, round_id)
    manifest = STORAGE.load_manifest(str(item["case_id"]), mock_only=False)
    query_start, query_end = checked_window(manifest, start, end)
    limit, offset = STORAGE.validate_page(page, page_size)
    try:
        async with LIMITER.slot(f"{identity.team_id}:final:{round_id}"):
            rows = await STORAGE.query_metrics(manifest, start=query_start, end=query_end, metric_name=metric_name, service=service, pod=pod, node=node, aggregation=aggregation, step_seconds=step_seconds, limit=limit, offset=offset)
    except ConcurrencyLimitError as exc:
        raise StorageError("CONCURRENCY_LIMITED", str(exc), 429) from exc
    FINAL_ROUNDS.authorize(request, round_id)
    return page_result(str(item["case_id"]), page, page_size, rows)


@app.get("/v1/final/rounds/{round_id}/case/logs", tags=["final"])
async def final_logs(request: Request, round_id: str, start: datetime | None = None, end: datetime | None = None, service: ShortText = None, pod: ShortText = None, severity: ShortText = None, trace_id: ShortText = None, keyword: Annotated[str | None, Query(max_length=256)] = None, page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1)] = 100):
    identity, item = await authorized_final(request, round_id)
    manifest = STORAGE.load_manifest(str(item["case_id"]), mock_only=False)
    query_start, query_end = checked_window(manifest, start, end)
    limit, offset = STORAGE.validate_page(page, page_size)
    try:
        async with LIMITER.slot(f"{identity.team_id}:final:{round_id}"):
            rows = await STORAGE.query_logs(manifest, start=query_start, end=query_end, service=service, pod=pod, severity=severity, trace_id=trace_id, keyword=keyword, limit=limit, offset=offset)
    except ConcurrencyLimitError as exc:
        raise StorageError("CONCURRENCY_LIMITED", str(exc), 429) from exc
    FINAL_ROUNDS.authorize(request, round_id)
    return page_result(str(item["case_id"]), page, page_size, rows)


@app.get("/v1/final/rounds/{round_id}/case/traces", tags=["final"])
async def final_traces(request: Request, round_id: str, start: datetime | None = None, end: datetime | None = None, service: ShortText = None, status: ShortText = None, operation_name: ShortText = None, parent_service: ShortText = None, page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1)] = 100):
    identity, item = await authorized_final(request, round_id)
    manifest = STORAGE.load_manifest(str(item["case_id"]), mock_only=False)
    query_start, query_end = checked_window(manifest, start, end)
    limit, offset = STORAGE.validate_page(page, page_size)
    try:
        async with LIMITER.slot(f"{identity.team_id}:final:{round_id}"):
            rows = await STORAGE.query_traces(manifest, start=query_start, end=query_end, service=service, status=status, operation_name=operation_name, parent_service=parent_service, limit=limit, offset=offset)
    except ConcurrencyLimitError as exc:
        raise StorageError("CONCURRENCY_LIMITED", str(exc), 429) from exc
    FINAL_ROUNDS.authorize(request, round_id)
    return page_result(str(item["case_id"]), page, page_size, rows)


@app.get("/v1/final/rounds/{round_id}/case/traces/{trace_id}", tags=["final"])
async def final_trace_detail(request: Request, round_id: str, trace_id: str):
    identity, item = await authorized_final(request, round_id)
    manifest = STORAGE.load_manifest(str(item["case_id"]), mock_only=False)
    try:
        async with LIMITER.slot(f"{identity.team_id}:final:{round_id}"):
            rows = await STORAGE.get_trace(manifest, trace_id)
    except ConcurrencyLimitError as exc:
        raise StorageError("CONCURRENCY_LIMITED", str(exc), 429) from exc
    FINAL_ROUNDS.authorize(request, round_id)
    return {"case_id": item["case_id"], "trace_id": trace_id, "spans": rows}


@app.get("/v1/{mode}/cases", tags=["cases"])
@app.get("/v1/cases", tags=["legacy"])
async def list_cases(request: Request, mode: str = "legacy"):
    mode = normalized_mode(mode)
    identity = await authorized(request, mode)
    # Use the request-scoped visibility computed by AccessController.  For a
    # normal team this is exactly the published set; for the bound organizer
    # test identity it additionally contains isolated internal-preview cases.
    # Reading the global published set here made the Question API and Case API
    # disagree during an authorized preview run.
    visible = ACCESS.authorized_cases(request, mode)
    items = [item for item in STORAGE.list_cases(mock_only=identity.mock_only) if item.get("case_id") in visible]
    return {"items": items, "mode": mode.upper()}


@app.get("/v1/{mode}/cases/{case_id}/metadata", tags=["cases"])
@app.get("/v1/cases/{case_id}/metadata", tags=["legacy"])
async def metadata(request: Request, case_id: str, mode: str = "legacy"):
    mode = normalized_mode(mode)
    identity = await authorized(request, mode, case_id)
    return STORAGE.public_metadata(STORAGE.load_manifest(case_id, mock_only=identity.mock_only))


@app.get("/v1/{mode}/cases/{case_id}/topology", tags=["cases"])
@app.get("/v1/cases/{case_id}/topology", tags=["legacy"])
async def topology(request: Request, case_id: str, mode: str = "legacy"):
    mode = normalized_mode(mode)
    identity = await authorized(request, mode, case_id)
    manifest = STORAGE.load_manifest(case_id, mock_only=identity.mock_only)
    return {"case_id": case_id, "topology": manifest["public_topology"]}


@app.get("/v1/{mode}/cases/{case_id}/metrics", tags=["observability"])
@app.get("/v1/cases/{case_id}/metrics", tags=["legacy"])
async def metrics(request: Request, case_id: str, mode: str = "legacy", start: datetime | None = None, end: datetime | None = None, metric_name: ShortText = None, service: ShortText = None, pod: ShortText = None, node: ShortText = None, aggregation: Annotated[str, Query(pattern="^(raw|avg|min|max|sum)$")] = "raw", step_seconds: Annotated[int, Query(ge=10, le=3600)] = 60, page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1)] = 100):
    mode = normalized_mode(mode)
    identity = await authorized(request, mode, case_id)
    manifest = STORAGE.load_manifest(case_id, mock_only=identity.mock_only)
    query_start, query_end = checked_window(manifest, start, end)
    limit, offset = STORAGE.validate_page(page, page_size)
    try:
        async with LIMITER.slot(f"{identity.team_id}:{mode}"):
            items = await STORAGE.query_metrics(manifest, start=query_start, end=query_end, metric_name=metric_name, service=service, pod=pod, node=node, aggregation=aggregation, step_seconds=step_seconds, limit=limit, offset=offset)
    except ConcurrencyLimitError as exc:
        raise StorageError("CONCURRENCY_LIMITED", str(exc), 429) from exc
    ACCESS.authorize(request, mode, case_id)
    return page_result(case_id, page, page_size, items)


@app.get("/v1/{mode}/cases/{case_id}/logs", tags=["observability"])
@app.get("/v1/cases/{case_id}/logs", tags=["legacy"])
async def logs(request: Request, case_id: str, mode: str = "legacy", start: datetime | None = None, end: datetime | None = None, service: ShortText = None, pod: ShortText = None, severity: ShortText = None, trace_id: ShortText = None, keyword: Annotated[str | None, Query(max_length=256)] = None, page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1)] = 100):
    mode = normalized_mode(mode)
    identity = await authorized(request, mode, case_id)
    manifest = STORAGE.load_manifest(case_id, mock_only=identity.mock_only)
    query_start, query_end = checked_window(manifest, start, end)
    limit, offset = STORAGE.validate_page(page, page_size)
    try:
        async with LIMITER.slot(f"{identity.team_id}:{mode}"):
            items = await STORAGE.query_logs(manifest, start=query_start, end=query_end, service=service, pod=pod, severity=severity, trace_id=trace_id, keyword=keyword, limit=limit, offset=offset)
    except ConcurrencyLimitError as exc:
        raise StorageError("CONCURRENCY_LIMITED", str(exc), 429) from exc
    ACCESS.authorize(request, mode, case_id)
    return page_result(case_id, page, page_size, items)


@app.get("/v1/{mode}/cases/{case_id}/traces", tags=["observability"])
@app.get("/v1/cases/{case_id}/traces", tags=["legacy"])
async def traces(request: Request, case_id: str, mode: str = "legacy", start: datetime | None = None, end: datetime | None = None, service: ShortText = None, status: ShortText = None, operation_name: ShortText = None, parent_service: ShortText = None, page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1)] = 100):
    mode = normalized_mode(mode)
    identity = await authorized(request, mode, case_id)
    manifest = STORAGE.load_manifest(case_id, mock_only=identity.mock_only)
    query_start, query_end = checked_window(manifest, start, end)
    limit, offset = STORAGE.validate_page(page, page_size)
    try:
        async with LIMITER.slot(f"{identity.team_id}:{mode}"):
            items = await STORAGE.query_traces(manifest, start=query_start, end=query_end, service=service, status=status, operation_name=operation_name, parent_service=parent_service, limit=limit, offset=offset)
    except ConcurrencyLimitError as exc:
        raise StorageError("CONCURRENCY_LIMITED", str(exc), 429) from exc
    ACCESS.authorize(request, mode, case_id)
    return page_result(case_id, page, page_size, items)


@app.get("/v1/{mode}/cases/{case_id}/traces/{trace_id}", tags=["observability"])
@app.get("/v1/cases/{case_id}/traces/{trace_id}", tags=["legacy"])
async def trace_detail(request: Request, case_id: str, trace_id: str, mode: str = "legacy"):
    mode = normalized_mode(mode)
    identity = await authorized(request, mode, case_id)
    manifest = STORAGE.load_manifest(case_id, mock_only=identity.mock_only)
    try:
        async with LIMITER.slot(f"{identity.team_id}:{mode}"):
            items = await STORAGE.get_trace(manifest, trace_id)
    except ConcurrencyLimitError as exc:
        raise StorageError("CONCURRENCY_LIMITED", str(exc), 429) from exc
    ACCESS.authorize(request, mode, case_id)
    return {"case_id": case_id, "trace_id": trace_id, "spans": items}
