from __future__ import annotations

import hmac
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Request

from .security import TeamIdentity, derive_hash
from .storage import StorageError


MODES = {"practice", "semifinal"}
PRACTICE_CASES = {"SF-001", "SF-002"}
SEMIFINAL_CASES = {f"SF-{index:03d}" for index in range(3, 13)}
SESSION_STATES = {"NOT_OPEN", "OPEN", "CLOSED", "EMERGENCY_LOCKED"}
REGISTERED_TEAM_ID = re.compile(r"TEAM-(0[1-9]|1[0-7])\Z")


class ReloadingJson:
    def __init__(self, path: Path):
        self.path = path
        self._mtime_ns = -1
        self._payload: dict[str, Any] = {}

    def read(self) -> dict[str, Any]:
        stat = self.path.stat()
        if stat.st_mtime_ns != self._mtime_ns:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"invalid JSON object: {self.path}")
            self._payload = payload
            self._mtime_ns = stat.st_mtime_ns
        return self._payload


class AccessController:
    """Server-trusted token, mode, session and publication authorization."""

    def __init__(self, registry_path: Path, state_path: Path):
        self.registry = ReloadingJson(registry_path)
        self.state = ReloadingJson(state_path)

    def authenticate(self, request: Request) -> TeamIdentity:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise StorageError("AUTH_REQUIRED", "a valid team token is required", 401)
        token = header[7:].strip()
        parts = token.split(".", 2)
        if len(parts) != 3:
            raise StorageError("AUTH_FAILED", "team credentials are invalid", 401)
        _, token_id, secret = parts
        try:
            records = self.registry.read().get("teams", [])
            record = next(item for item in records if item.get("token_id") == token_id)
            if record.get("token_status") != "ACTIVE":
                raise ValueError("inactive token")
            candidate = derive_hash(secret, str(record["token_salt"]))
            if not hmac.compare_digest(candidate, str(record["token_hash"])):
                raise ValueError("hash mismatch")
        except (OSError, KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StorageError("AUTH_FAILED", "team credentials are invalid", 401) from exc
        identity = TeamIdentity(
            token_id=str(record["token_id"]),
            team_id=str(record["team_id"]),
            mock_only=bool(record.get("mock_only", False)),
            team_name=str(record.get("team_name", "")),
            assigned_ip=record.get("assigned_ip"),
            session=str(record.get("session", "UNASSIGNED")),
            allowed_modes=tuple(str(value).upper() for value in record.get("allowed_modes", [])),
            is_organizer_test=bool(record.get("is_organizer_test", False)),
        )
        request.state.team_id = identity.team_id
        request.state.token_id = identity.token_id
        request.state.assigned_ip = identity.assigned_ip
        return identity

    @staticmethod
    def _canonical_mode(mode: str) -> str:
        value = mode.lower()
        if value not in MODES:
            raise StorageError("MODE_NOT_FOUND", "competition mode does not exist", 404)
        return value

    @staticmethod
    def _case_allowed_for_mode(mode: str, case_id: str) -> bool:
        return case_id in (PRACTICE_CASES if mode == "practice" else SEMIFINAL_CASES)

    @staticmethod
    def _closed_practice_test_access(request: Request, identity: TeamIdentity) -> bool:
        """Allow the dedicated organizer test identity only from its bound host.

        This keeps practice closed for every participant while permitting the
        organizer's isolated test machine to exercise the real Public API.
        """
        source_ip = str(getattr(request.state, "source_ip", ""))
        assigned_ip = str(identity.assigned_ip or "")
        return bool(
            identity.is_organizer_test
            and assigned_ip
            and source_ip
            and hmac.compare_digest(source_ip, assigned_ip)
        )

    def _team_practice_test_cases(self, request: Request, identity: TeamIdentity) -> set[str]:
        if identity.is_organizer_test or not identity.assigned_ip:
            return set()
        source_ip = str(getattr(request.state, "source_ip", ""))
        if not source_ip or not hmac.compare_digest(source_ip, identity.assigned_ip):
            return set()
        records = self.registry.read().get("teams", [])
        record = next((item for item in records if item.get("token_id") == identity.token_id), {})
        grant = record.get("practice_test_access")
        if not isinstance(grant, dict) or grant.get("enabled") is not True:
            return set()
        try:
            expires_at = datetime.fromisoformat(str(grant["expires_at_utc"]).replace("Z", "+00:00"))
            if expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc):
                return set()
            cases = grant["cases"]
            if not isinstance(cases, list) or any(not isinstance(case, str) for case in cases):
                return set()
        except (KeyError, TypeError, ValueError):
            return set()
        return set(cases) & PRACTICE_CASES

    @staticmethod
    def _am_preview_test_cases(identity: TeamIdentity, semifinal: dict[str, Any]) -> set[str]:
        grant = semifinal.get("am_preview_test")
        if identity.session != "AM" or not isinstance(grant, dict) or grant.get("enabled") is not True:
            return set()
        try:
            expires_at = datetime.fromisoformat(str(grant["expires_at_utc"]).replace("Z", "+00:00"))
            teams, cases = grant["teams"], grant["cases"]
            if (expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc)
                    or not isinstance(teams, list) or len(teams) != len(set(teams))
                    or not all(isinstance(team, str) for team in teams)
                    or identity.team_id not in teams
                    or not isinstance(cases, list) or not all(isinstance(case, str) for case in cases)):
                return set()
        except (KeyError, TypeError, ValueError):
            return set()
        return set(cases) & set(semifinal.get("internal_preview_cases", [])) & SEMIFINAL_CASES

    def authorize(
        self,
        request: Request,
        mode: str,
        case_id: str | None = None,
    ) -> TeamIdentity:
        mode = self._canonical_mode(mode)
        identity = self.authenticate(request)
        request.state.competition_mode = mode.upper()
        request.state.case_id = case_id
        if REGISTERED_TEAM_ID.fullmatch(identity.team_id):
            source_ip = str(getattr(request.state, "source_ip", ""))
            assigned_ip = str(identity.assigned_ip or "")
            if not source_ip or not assigned_ip or not hmac.compare_digest(source_ip, assigned_ip):
                raise StorageError("SOURCE_IP_MISMATCH", "team credential is not valid from this host", 403)
        if mode.upper() not in identity.allowed_modes:
            raise StorageError("ACCESS_DENIED", "competition mode is not authorized", 403)
        state = self.state.read()
        if mode == "practice":
            practice = state.get("practice", {})
            published = set(practice.get("published_cases", []))
            if practice.get("state") != "OPEN":
                if not self._closed_practice_test_access(request, identity):
                    test_cases = self._team_practice_test_cases(request, identity)
                    if not test_cases:
                        raise StorageError("PRACTICE_NOT_OPEN", "practice access is not open", 403)
                    published &= test_cases
        else:
            semifinal = state.get("semifinal", {})
            organizer_preview = self._closed_practice_test_access(request, identity)
            if organizer_preview:
                published = set(semifinal.get("published_cases", [])) | set(semifinal.get("internal_preview_cases", []))
                request.state.internal_preview = True
            else:
                if identity.session not in {"AM", "PM"}:
                    raise StorageError("SESSION_UNASSIGNED", "team session has not been assigned", 403)
                session = semifinal.get("sessions", {}).get(identity.session, {})
                if session.get("state") != "OPEN":
                    raise StorageError("SESSION_NOT_OPEN", "the assigned semifinal session is not open", 403)
                preview_cases = self._am_preview_test_cases(identity, semifinal)
                published = set(semifinal.get("published_cases", [])) | preview_cases
                request.state.internal_preview = bool(preview_cases)
                request.state.am_preview_test = bool(preview_cases)
        request.state.authorized_cases = sorted(published)
        if case_id is not None:
            if not self._case_allowed_for_mode(mode, case_id) or case_id not in published:
                # Deliberately indistinguishable from a nonexistent case.
                raise StorageError("CASE_NOT_FOUND", "case is not published or not authorized", 404)
        return identity

    def authorized_cases(self, request: Request, mode: str) -> set[str]:
        """Return the exact cases selected by the most recent authorization.

        This is request-scoped so internal preview Cases can never leak through
        the global published_cases helper used by participant traffic.
        """
        self._canonical_mode(mode)
        return set(getattr(request.state, "authorized_cases", []))

    def published_cases(self, mode: str) -> set[str]:
        mode = self._canonical_mode(mode)
        state = self.state.read()
        section = state[mode]
        return set(section.get("published_cases", []))
