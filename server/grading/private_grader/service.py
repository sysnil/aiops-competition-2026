from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from .llm_judge import JudgeConfig, LLMJudge
from .judge_store import JudgeResultStore
from .scoring import score_answer
from .qa_scoring import score_qa_answer
from .final_scoring import normalize_draft, score_final


class GradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    submission_id: str
    case_id: str
    answer: dict[str, Any]
    evidence_validation: dict[str, Any]
    grading_mode: str


GT_DIRECTORY = Path(os.environ.get("PRIVATE_GRADER_GT_DIR", "/etc/aiops-private-grader/gt"))
PREVIEW_GT_DIRECTORY = Path(os.environ.get("PRIVATE_GRADER_PREVIEW_GT_DIR", "/etc/aiops-private-grader/gt-preview"))
QA_GT_DIRECTORY = Path(os.environ.get("PRIVATE_GRADER_QA_GT_DIR", "/etc/aiops-private-grader/qa-gt"))
QA_PREVIEW_GT_DIRECTORY = Path(os.environ.get("PRIVATE_GRADER_QA_PREVIEW_GT_DIR", "/etc/aiops-private-grader/qa-gt-preview"))
FINAL_PREVIEW_GT_DIRECTORY = Path(os.environ.get("PRIVATE_GRADER_FINAL_PREVIEW_GT_DIR", "/etc/aiops-private-grader/final-gt-preview"))
FINAL_GT_DIRECTORY = Path(os.environ.get("PRIVATE_GRADER_FINAL_GT_DIR", "/etc/aiops-private-grader/final-gt"))
FINAL_GT_MANIFEST_SHA256 = os.environ.get("PRIVATE_GRADER_FINAL_GT_MANIFEST_SHA256", "").strip()
FINAL_RUBRIC_PATH = Path(os.environ.get("PRIVATE_GRADER_FINAL_RUBRIC_PATH", "/etc/aiops-private-grader/formal-final-rubric-v2.json"))
FINAL_RUBRIC_SHA256 = os.environ.get("PRIVATE_GRADER_FINAL_RUBRIC_SHA256", "").strip()
FINAL_HYBRID_ROOT = Path("/etc/aiops-private-grader/final-hybrid-gt/FINAL-STAGE3-HYBRID-GT")
FINAL_HYBRID_MANIFEST_SHA256 = "2a84374ba13b7702aa5175b68724748dd6aca0c20c1a7370f1031f0892ab61d5"
SHARED_TOKEN = os.environ.get("GRADER_SHARED_TOKEN", "")
JUDGE = LLMJudge(JudgeConfig.from_env())
JUDGE_STORE = JudgeResultStore(Path(os.environ.get(
    "PRIVATE_GRADER_STATE_DB", "/var/lib/aiops-private-grader/judge-results.sqlite3"
)))
JUDGE_SEMAPHORE = asyncio.Semaphore(max(1, int(os.environ.get("JUDGE_CONCURRENCY", "2"))))
SUBMISSION_LOCKS: dict[str, asyncio.Lock] = {}

app = FastAPI(
    title="AIOps Private Grader",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def service_auth(authorization: str = Header(default="")) -> None:
    if not SHARED_TOKEN or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="service authentication required")
    if not hmac.compare_digest(authorization[7:].strip(), SHARED_TOKEN):
        raise HTTPException(status_code=401, detail="service authentication failed")


def load_gt(case_id: str, schema_version: str, grading_mode: str) -> dict[str, Any] | None:
    if not case_id.replace("-", "").replace("_", "").isalnum():
        return None
    directory = PREVIEW_GT_DIRECTORY if grading_mode == "PREVIEW" else GT_DIRECTORY
    suffix = "-v3" if case_id == "DEMO-001" and schema_version == "3.0" else ""
    path = (directory / f"{case_id}{suffix}.json").resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("case_id") != case_id:
        return None
    return payload


def load_qa_gt(question_id: str, grading_mode: str) -> dict[str, Any] | None:
    if not question_id.replace("-", "").replace("_", "").isalnum():
        return None
    directory = QA_PREVIEW_GT_DIRECTORY if grading_mode == "PREVIEW" else QA_GT_DIRECTORY
    path = (directory / f"{question_id}.json").resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("question_id") != question_id:
        return None
    return payload


def load_final_hybrid_gt(case_id: str) -> dict[str, Any] | None:
    if not case_id.startswith("SF-") or not case_id[3:].isdigit():
        return None
    index_path = FINAL_HYBRID_ROOT / "gt-freeze-manifest.json"
    if index_path.is_symlink() or not index_path.is_file():
        return None
    index_bytes = index_path.read_bytes()
    if hashlib.sha256(index_bytes).hexdigest() != FINAL_HYBRID_MANIFEST_SHA256:
        return None
    index = json.loads(index_bytes)
    if index.get("batch_id") != "FINAL-STAGE3-HYBRID-GT" or index.get("formal_gt_approved") is not False:
        return None
    row = next((item for item in index.get("cases", []) if item.get("case_id") == case_id), None)
    if not row or row.get("snapshot_file") != f"{case_id}.json":
        return None
    path = (FINAL_HYBRID_ROOT / row["snapshot_file"]).resolve()
    if not path.is_relative_to(FINAL_HYBRID_ROOT.resolve()) or path.is_symlink() or not path.is_file():
        return None
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row.get("snapshot_sha256"):
        return None
    payload = json.loads(raw)
    if (payload.get("case_id") != case_id or payload.get("batch_id") != index["batch_id"] or
            payload.get("classification") != "HYBRID_INTERNAL_PREVIEW_ONLY" or
            payload.get("status") != "DRAFT_PREVIEW_ONLY"):
        return None
    return payload


def load_approved_final_gt(case_id: str) -> dict[str, Any] | None:
    if not case_id.startswith("SF-") or not case_id[3:].isdigit() or len(FINAL_GT_MANIFEST_SHA256) != 64:
        return None
    manifest_path = FINAL_GT_DIRECTORY / "gt-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return None
    raw_manifest = manifest_path.read_bytes()
    if hashlib.sha256(raw_manifest).hexdigest() != FINAL_GT_MANIFEST_SHA256:
        return None
    manifest = json.loads(raw_manifest)
    if manifest.get("classification") != "PRIVATE_OFFICIAL_FINAL_GT" or manifest.get("review_status") != "APPROVED":
        return None
    entries = [item for item in manifest.get("cases", []) if item.get("case_id") == case_id]
    if len(entries) != 1 or entries[0].get("file") != f"{case_id}.json":
        return None
    path = FINAL_GT_DIRECTORY / entries[0]["file"]
    if path.is_symlink() or not path.is_file():
        return None
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entries[0].get("sha256"):
        return None
    gt = json.loads(raw)
    if (gt.get("case_id") != case_id or gt.get("review_status") != "APPROVED"
            or gt.get("status") != "APPROVED" or gt.get("classification") != "OFFICIAL"
            or gt.get("version") != manifest.get("version")
            or gt.get("matching_rule_version") != manifest.get("scoring_rule_version")
            or len(gt.get("events", [])) != entries[0].get("events")):
        return None
    return gt


def load_formal_final_rubric() -> dict[str, Any] | None:
    if len(FINAL_RUBRIC_SHA256) != 64 or FINAL_RUBRIC_PATH.is_symlink() or not FINAL_RUBRIC_PATH.is_file():
        return None
    raw = FINAL_RUBRIC_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != FINAL_RUBRIC_SHA256:
        return None
    policy = json.loads(raw)
    if (policy.get("status") != "ACTIVE_FORMAL"
            or policy.get("applies_to") != "FUTURE_OFFICIAL_FINAL_SUBMISSIONS_ALL_TEAMS"
            or policy.get("approved_gt_manifest_sha256") != FINAL_GT_MANIFEST_SHA256
            or policy.get("version") != "final-balanced-evidence-v2.0"
            or policy.get("max_points") != 10.0
            or policy.get("strict_positive_credit") is not True
            or policy.get("empty_answer_score") != 0.0
            or sum(policy.get(key, 0) for key in ("type", "entity", "time", "evidence")) != 100
            or policy.get("time_tolerance_seconds") != 300
            or policy.get("false_positive_penalty") != 2.0
            or policy.get("duplicate_penalty") != 2.0):
        return None
    return {key: policy[key] for key in (
        "version", "max_points", "strict_positive_credit", "type", "entity", "time", "evidence",
        "time_tolerance_seconds", "false_positive_penalty", "duplicate_penalty",
    )}


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "service": "aiops-private-grader",
        "version": app.version,
        "judge_model_configured": bool(JUDGE.config.model and JUDGE.config.url and JUDGE.config.api_key),
    }


@app.post("/internal/v1/grade", dependencies=[Depends(service_auth)])
async def grade(request: GradeRequest):
    schema_version = str(request.answer.get("schema_version", ""))
    task_type = str(request.answer.get("task_type", ""))
    if task_type == "FINAL_ANOMALY_INVESTIGATION":
        if schema_version != "final-1.0" or not request.evidence_validation.get("valid"):
            return {"status": "REJECTED"}
        if not request.case_id.replace("-", "").isalnum():
            return {"status": "PENDING_GT_APPROVAL"}
        # Organizer previews use the same approved GT and active rubric as
        # official submissions. Preview remains excluded from competition results.
        # Historical Stage2/Stage3 named batches retain their frozen semantics.
        if request.grading_mode == "PREVIEW":
            gt = load_approved_final_gt(request.case_id)
            if gt is None:
                return {"status": "PENDING_GT_APPROVAL"}
            rubric = load_formal_final_rubric()
            if rubric is None:
                return {"status": "PENDING_REVIEW", "review_status": "FORMAL_RUBRIC_UNAVAILABLE"}
            result = score_final(request.answer, gt, request.evidence_validation, rubric=rubric)
            result["status"] = "PREVIEW_COMPLETED"
            result["grading_mode"] = "PREVIEW"
            result["scoring_profile"] = "CURRENT_APPROVED_FINAL"
            return result
        if request.grading_mode == "HYBRID_INTERNAL_PREVIEW":
            gt = load_final_hybrid_gt(request.case_id)
            if gt is None:
                return {"status": "PENDING_GT_APPROVAL"}
            result = score_final(request.answer, gt, request.evidence_validation,
                                 rubric={"version": "final-stage3-hybrid-preview-1.0"})
            result["review_status"] = "HYBRID_GT_INTERNAL_PREVIEW_ONLY"
            result["grading_mode"] = "HYBRID_INTERNAL_PREVIEW"
            return result
        is_internal_preview = request.grading_mode == "INTERNAL_PREVIEW"
        is_preview = request.grading_mode in {"PREVIEW", "INTERNAL_PREVIEW"}
        if is_preview:
            directory = FINAL_PREVIEW_GT_DIRECTORY
            path = (directory / f"{request.case_id}.json").resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file():
                return {"status": "PENDING_GT_APPROVAL"}
            gt = json.loads(path.read_text(encoding="utf-8"))
            gt = normalize_draft(
                gt,
                request.case_id,
                prefer_merged=is_internal_preview,
                version="final-stage2-auto-gt-frozen-v1" if is_internal_preview else "DRAFT_PREVIEW_ONLY",
            )
        else:
            gt = load_approved_final_gt(request.case_id)
            if gt is None:
                return {"status": "PENDING_GT_APPROVAL"}
        if is_internal_preview:
            rubric = {"version": "final-stage2-auto-gt-1.0"}
        elif is_preview:
            rubric = None
        else:
            rubric = load_formal_final_rubric()
            if rubric is None:
                return {"status": "PENDING_REVIEW", "review_status": "FORMAL_RUBRIC_UNAVAILABLE"}
        result = score_final(request.answer, gt, request.evidence_validation, rubric=rubric)
        if is_internal_preview:
            result["review_status"] = "AUTO_GT_INTERNAL_PREVIEW_ONLY"
        result["grading_mode"] = request.grading_mode if is_preview else "OFFICIAL"
        return result
    if task_type == "SEMIFINAL_QUERY_VISUALIZATION":
        question_id = str(request.answer.get("question_id", ""))
        gt = load_qa_gt(question_id, request.grading_mode)
        is_preview = request.grading_mode == "PREVIEW"
        if gt is None or (not is_preview and gt.get("review_status") != "APPROVED"):
            return {"status": "PENDING_GT_APPROVAL"}
        if gt.get("case_id") != request.case_id or gt.get("task_type") != task_type:
            return {"status": "PENDING_GT_APPROVAL"}
        if is_preview and gt.get("classification") != "DRAFT_PREVIEW_ONLY":
            return {"status": "PENDING_GT_APPROVAL"}
        result = score_qa_answer(request.answer, gt)
        if is_preview:
            result["status"] = "PREVIEW_COMPLETED"
            result["review_status"] = "PREVIEW_ONLY"
        result["grading_mode"] = "PREVIEW" if is_preview else "OFFICIAL"
        return result
    gt = load_gt(request.case_id, schema_version, request.grading_mode)
    is_preview = request.grading_mode == "PREVIEW"
    if gt is None or (not is_preview and gt.get("review_status") != "APPROVED"):
        return {"status": "PENDING_GT_APPROVAL"}
    if gt.get("classification") == "SYNTHETIC_DEMO_ONLY" and request.grading_mode != "DEMO":
        return {"status": "PENDING_GT_APPROVAL"}
    if schema_version == "3.0" and gt.get("task_type") != "SEMIFINAL_ANOMALY_DETECTION":
        return {"status": "PENDING_GT_APPROVAL"}
    # Serialize duplicate requests for one submission while allowing different
    # teams to proceed under the global bounded Judge semaphore.
    lock = SUBMISSION_LOCKS.setdefault(request.submission_id, asyncio.Lock())
    async with lock:
        result = await score_answer(
            request.answer,
            gt,
            request.evidence_validation,
            JUDGE,
            allow_preview=is_preview,
            submission_id=request.submission_id,
            judge_store=JUDGE_STORE,
            judge_semaphore=JUDGE_SEMAPHORE,
        )
    if result.get("status") == "COMPLETED" and is_preview:
        result["status"] = "PREVIEW_COMPLETED"
        result["review_status"] = "PREVIEW_ONLY"
    result["grading_mode"] = (
        "PREVIEW" if is_preview else
        "DEMO" if gt.get("classification") == "SYNTHETIC_DEMO_ONLY" else
        "OFFICIAL"
    )
    return result
