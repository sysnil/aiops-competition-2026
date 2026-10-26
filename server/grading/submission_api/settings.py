from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GradingSettings:
    database_path: Path
    submission_dir: Path
    token_file: Path
    grader_url: str
    grader_token: str
    max_body_bytes: int = 131_072
    max_qa_body_bytes: int = 2_500_000
    max_official_submissions: int = 3
    grader_timeout_seconds: float = 30.0
    grader_retries: int = 2
    worker_concurrency: int = 2
    judge_task_retries: int = 2
    judge_retry_backoff_seconds: float = 2.0


def load_grading_settings() -> GradingSettings:
    return GradingSettings(
        database_path=Path(os.environ.get("GRADING_DATABASE", "/var/lib/aiops-grading/submissions.sqlite3")),
        submission_dir=Path(os.environ.get("GRADING_SUBMISSION_DIR", "/var/lib/aiops-grading/answers")),
        token_file=Path(os.environ.get("GRADING_TOKEN_FILE", "/etc/aiops-contest/submission_tokens.json")),
        grader_url=os.environ.get("GRADER_URL", "http://127.0.0.1:9001").rstrip("/"),
        grader_token=os.environ.get("GRADER_SHARED_TOKEN", ""),
        max_body_bytes=int(os.environ.get("GRADING_MAX_BODY_BYTES", "131072")),
        max_qa_body_bytes=int(os.environ.get("GRADING_MAX_QA_BODY_BYTES", "2500000")),
        max_official_submissions=int(os.environ.get("GRADING_MAX_OFFICIAL_SUBMISSIONS", "3")),
        grader_timeout_seconds=float(os.environ.get("GRADER_TIMEOUT_SECONDS", "30")),
        grader_retries=int(os.environ.get("GRADER_RETRIES", "2")),
        worker_concurrency=int(os.environ.get("GRADING_WORKER_CONCURRENCY", "2")),
        judge_task_retries=int(os.environ.get("GRADING_JUDGE_TASK_RETRIES", "2")),
        judge_retry_backoff_seconds=float(os.environ.get("GRADING_JUDGE_RETRY_BACKOFF_SECONDS", "2")),
    )
