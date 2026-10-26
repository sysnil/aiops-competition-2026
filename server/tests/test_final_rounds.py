from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.access_control import AccessController
from app.final_rounds import FinalRoundController
from app.security import derive_hash
from app.storage import StorageError


class FakeRequest:
    def __init__(self, token: str, source_ip: str):
        self.headers = {"authorization": f"Bearer {token}"}
        self.state = SimpleNamespace(source_ip=source_ip)


def setup_controller(tmp_path):
    salt = "22" * 16
    secret = "round-secret"
    token = f"team.FINAL01.{secret}"
    other = f"team.FINAL02.{secret}"
    registry = {"teams": [
        {"token_id": "FINAL01", "token_salt": salt, "token_hash": derive_hash(secret, salt), "token_status": "ACTIVE", "team_id": "TEAM-01", "assigned_ip": "192.168.51.52", "session": "AM", "allowed_modes": ["PRACTICE", "SEMIFINAL", "FINAL"]},
        {"token_id": "FINAL02", "token_salt": salt, "token_hash": derive_hash(secret, salt), "token_status": "ACTIVE", "team_id": "TEAM-02", "assigned_ip": "192.168.51.53", "session": "AM", "allowed_modes": ["PRACTICE", "SEMIFINAL", "FINAL"]},
    ]}
    state = {"practice": {"state": "CLOSED", "published_cases": ["SF-001"]}, "semifinal": {"published_cases": ["SF-003"], "sessions": {"AM": {"state": "NOT_OPEN"}, "PM": {"state": "NOT_OPEN"}}}}
    now = datetime.now(timezone.utc)
    rounds = {"rounds": [{"round_id": "R01", "status": "OPEN", "champion_team_id": "TEAM-01", "challenger_team_id": "TEAM-09", "case_id": "SF-020", "opens_at": (now - timedelta(minutes=1)).isoformat(), "closes_at": (now + timedelta(minutes=10)).isoformat()}]}
    registry_path, state_path, rounds_path = tmp_path / "registry.json", tmp_path / "state.json", tmp_path / "rounds.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    rounds_path.write_text(json.dumps(rounds), encoding="utf-8")
    access = AccessController(registry_path, state_path)
    return FinalRoundController(rounds_path, access), token, other


def test_only_participant_from_bound_host_can_access(tmp_path):
    controller, token, _ = setup_controller(tmp_path)
    identity, item = controller.authorize(FakeRequest(token, "192.168.51.52"), "R01")
    assert identity.team_id == "TEAM-01"
    assert item["case_id"] == "SF-020"


@pytest.mark.parametrize("source_ip", ["192.168.51.53", "192.168.51.99"])
def test_wrong_host_is_hidden(tmp_path, source_ip):
    controller, token, _ = setup_controller(tmp_path)
    with pytest.raises(StorageError) as caught:
        controller.authorize(FakeRequest(token, source_ip), "R01")
    assert caught.value.status_code == 404


def test_nonparticipant_is_hidden(tmp_path):
    controller, _, other = setup_controller(tmp_path)
    with pytest.raises(StorageError):
        controller.authorize(FakeRequest(other, "192.168.51.53"), "R01")


def test_previously_published_case_cannot_be_final_case(tmp_path):
    controller, token, _ = setup_controller(tmp_path)
    payload = controller.state.read()
    payload["rounds"][0]["case_id"] = "SF-003"
    controller.state.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(StorageError):
        controller.authorize(FakeRequest(token, "192.168.51.52"), "R01")
