from __future__ import annotations

import json
import secrets
from pathlib import Path

from app.security import derive_hash


def main() -> None:
    token_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    salt = secrets.token_hex(16)
    token = f"team.{token_id}.{secret}"
    token_file = Path("/etc/aiops-contest/submission_tokens.json")
    token_file.write_text(
        json.dumps(
            {
                "version": 1,
                "tokens": [
                    {
                        "token_id": token_id,
                        "team_id": "DEMO-TEAM-01",
                        "salt": salt,
                        "hash": derive_hash(secret, salt),
                        "enabled": True,
                        "mock_only": False,
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    token_file.chmod(0o640)
    env_file = Path("/root/demo_submission_token.env")
    env_file.write_text(f"AIOPS_SUBMISSION_TOKEN={token}\n", encoding="utf-8")
    env_file.chmod(0o600)
    print("created protected demo submission credentials")


if __name__ == "__main__":
    main()

