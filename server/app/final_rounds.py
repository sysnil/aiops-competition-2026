from __future__ import annotations

import hmac
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Request

from .access_control import AccessController, ReloadingJson
from .security import TeamIdentity
from .storage import StorageError


def _utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("final round times must include timezone")
    return parsed.astimezone(timezone.utc)


class FinalRoundController:
    """Authorize two-team final rounds without reusing semifinal state."""

    def __init__(self, path: Path, access: AccessController):
        self.state = ReloadingJson(path)
        self.access = access

    def _round(self, round_id: str) -> dict[str, Any]:
        rounds = self.state.read().get("rounds", [])
        item = next((value for value in rounds if value.get("round_id") == round_id), None)
        if not item:
            raise StorageError("ROUND_NOT_FOUND", "final round does not exist or is not authorized", 404)
        return item

    def current(self, request: Request) -> tuple[TeamIdentity, dict[str, Any]]:
        identity = self.access.authenticate(request)
        for item in self.state.read().get("rounds", []):
            if self._eligible(request, identity, item, require_open=True):
                return identity, item
        raise StorageError("ROUND_NOT_FOUND", "no open final round is authorized", 404)

    def authorize(self, request: Request, round_id: str, *, require_open: bool = True) -> tuple[TeamIdentity, dict[str, Any]]:
        identity = self.access.authenticate(request)
        item = self._round(round_id)
        if not self._eligible(request, identity, item, require_open=require_open):
            raise StorageError("ROUND_NOT_FOUND", "final round does not exist or is not authorized", 404)
        request.state.competition_mode = "FINAL"
        request.state.case_id = item.get("case_id")
        return identity, item

    @staticmethod
    def public(item: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "round_id", "status", "champion_team_id", "challenger_team_id",
            "case_id", "opens_at", "closes_at", "time_limit_seconds",
            "judge_weights", "root_cause_bonus_max",
        )
        return {key: item.get(key) for key in fields}

    def _eligible(self, request: Request, identity: TeamIdentity, item: dict[str, Any], *, require_open: bool) -> bool:
        participants = {str(item.get("champion_team_id") or ""), str(item.get("challenger_team_id") or "")}
        if identity.team_id not in participants and not identity.is_organizer_test:
            return False
        if not identity.is_organizer_test and "FINAL" not in identity.allowed_modes:
            return False
        source_ip = str(getattr(request.state, "source_ip", ""))
        if identity.assigned_ip and not hmac.compare_digest(source_ip, str(identity.assigned_ip)):
            return False
        if not require_open:
            return True
        if item.get("status") != "OPEN" or not item.get("case_id"):
            return False
        competition_state = self.access.state.read()
        already_exposed = set(competition_state.get("practice", {}).get("published_cases", []))
        already_exposed.update(competition_state.get("semifinal", {}).get("published_cases", []))
        if item.get("case_id") in already_exposed:
            return False
        now = datetime.now(timezone.utc)
        try:
            opens_at, closes_at = _utc(item.get("opens_at")), _utc(item.get("closes_at"))
        except ValueError:
            return False
        return bool(opens_at and closes_at and opens_at <= now < closes_at)
