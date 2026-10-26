from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.security import AuthenticationError, TokenStore
from app.storage import StorageError

from .settings import GradingSettings


@dataclass(frozen=True)
class SubmissionIdentity:
    token_id: str
    team_id: str


class SubmissionAuthenticator:
    def __init__(self, settings: GradingSettings):
        self.store = TokenStore(settings.token_file)

    def authenticate(self, request: Request) -> SubmissionIdentity:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise StorageError("SUBMISSION_AUTH_REQUIRED", "valid submission credentials are required", 401)
        token = header[7:].strip()
        try:
            identity = self.store.verify(token)
        except (AuthenticationError, OSError, ValueError) as exc:
            raise StorageError("SUBMISSION_AUTH_FAILED", "submission credentials are invalid", 401) from exc
        request.state.team_id = f"submission:{identity.team_id}"
        return SubmissionIdentity(identity.token_id, identity.team_id)

