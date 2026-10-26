"""Verify the committee review access code without storing a plain credential."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path


AUTH = Path(os.environ.get("SUBMISSION_REVIEW_AUTH", "/etc/aiops-submission-review/auth.json"))


def verify_password(password: str, path: Path = AUTH) -> bool:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        if config["algorithm"] != "pbkdf2_sha256" or config["iterations"] < 600_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(config["salt"]), config["iterations"])
        return hmac.compare_digest(actual, bytes.fromhex(config["digest"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False
