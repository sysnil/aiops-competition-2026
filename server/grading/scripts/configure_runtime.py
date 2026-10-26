from __future__ import annotations

import argparse
import getpass
import grp
import os
import secrets
from pathlib import Path


def protected_write(path: Path, text: str, group: str) -> None:
    path.write_text(text, encoding="utf-8")
    os.chown(path, 0, grp.getgrnam(group).gr_gid)
    path.chmod(0o640)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create protected gateway/grader environment files")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    api_key = getpass.getpass("GPT API key (input hidden): ").strip()
    if not api_key:
        raise SystemExit("API key is required")
    shared = secrets.token_urlsafe(48)
    protected_write(
        Path("/etc/aiops-contest/grading-gateway.env"),
        "\n".join(
            [
                "GRADING_DATABASE=/var/lib/aiops-grading/submissions.sqlite3",
                "GRADING_SUBMISSION_DIR=/var/lib/aiops-grading/answers",
                "GRADING_TOKEN_FILE=/etc/aiops-contest/submission_tokens.json",
                "GRADER_URL=http://127.0.0.1:9001",
                f"GRADER_SHARED_TOKEN={shared}",
                "GRADING_MAX_BODY_BYTES=131072",
                "GRADING_MAX_OFFICIAL_SUBMISSIONS=3",
                "GRADING_WORKER_CONCURRENCY=2",
                "GRADER_TIMEOUT_SECONDS=180",
                "GRADER_RETRIES=2",
                "GRADING_JUDGE_TASK_RETRIES=2",
                "GRADING_JUDGE_RETRY_BACKOFF_SECONDS=2",
                "",
            ]
        ),
        "aiops-api",
    )
    protected_write(
        Path("/etc/aiops-private-grader/grader.env"),
        "\n".join(
            [
                "PRIVATE_GRADER_GT_DIR=/etc/aiops-private-grader/gt",
                "PRIVATE_GRADER_PREVIEW_GT_DIR=/etc/aiops-private-grader/gt-preview",
                "PRIVATE_GRADER_STATE_DB=/var/lib/aiops-private-grader/judge-results.sqlite3",
                "JUDGE_CONCURRENCY=2",
                f"GRADER_SHARED_TOKEN={shared}",
                f"GPT_BASE_URL={args.base_url}",
                f"GPT_MODEL={args.model}",
                f"GPT_API_KEY={api_key}",
                "GPT_TIMEOUT_SECONDS=30",
                "GPT_MAX_OUTPUT_TOKENS=500",
                "GPT_RETRIES=2",
                "",
            ]
        ),
        "aiops-grader",
    )
    print("protected runtime configuration created; no secret values were printed")


if __name__ == "__main__":
    main()
