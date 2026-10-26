from __future__ import annotations

import json

import pytest
from starlette.requests import Request

from app.access_control import AccessController
from app.security import derive_hash
from app.storage import StorageError


def make_request(token: str, source_ip: str) -> Request:
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/v1/practice/cases",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "client": (source_ip, 12345),
    })
    request.state.source_ip = source_ip
    return request


def controller(tmp_path, *, organizer: bool, assigned_ip: str = "192.168.51.50") -> tuple[AccessController, str]:
    secret = "unit-test-secret"
    salt = "11" * 16
    token = f"organizer.TESTTOKEN.{secret}"
    registry = {
        "teams": [{
            "token_id": "TESTTOKEN",
            "token_salt": salt,
            "token_hash": derive_hash(secret, salt),
            "token_status": "ACTIVE",
            "team_id": "ORGANIZER-TEST" if organizer else "TEAM-01",
            "team_name": "test",
            "assigned_ip": assigned_ip,
            "session": "UNASSIGNED",
            "allowed_modes": ["PRACTICE", "SEMIFINAL"],
            "is_organizer_test": organizer,
        }]
    }
    state = {
        "practice": {"state": "CLOSED", "published_cases": ["SF-001", "SF-002"]},
        "semifinal": {"published_cases": [], "internal_preview_cases": ["SF-003", "SF-005", "SF-010"], "sessions": {"AM": {"state": "NOT_OPEN"}, "PM": {"state": "NOT_OPEN"}}},
    }
    registry_path = tmp_path / "registry.json"
    state_path = tmp_path / "state.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return AccessController(registry_path, state_path), token


def test_closed_practice_allows_bound_organizer_test_host(tmp_path):
    access, token = controller(tmp_path, organizer=True)
    identity = access.authorize(make_request(token, "192.168.51.50"), "practice", "SF-001")
    assert identity.team_id == "ORGANIZER-TEST"


@pytest.mark.parametrize("organizer,source_ip", [(False, "192.168.51.50"), (True, "192.168.51.99")])
def test_closed_practice_stays_closed_for_teams_and_wrong_source(tmp_path, organizer, source_ip):
    access, token = controller(tmp_path, organizer=organizer)
    with pytest.raises(StorageError) as captured:
        access.authorize(make_request(token, source_ip), "practice", "SF-001")
    assert captured.value.code == "PRACTICE_NOT_OPEN"


def test_bound_organizer_can_use_isolated_semifinal_preview(tmp_path):
    access, token = controller(tmp_path, organizer=True)
    request = make_request(token, "192.168.51.50")
    identity = access.authorize(request, "semifinal", "SF-003")
    assert identity.team_id == "ORGANIZER-TEST"
    assert access.authorized_cases(request, "semifinal") == {"SF-003", "SF-005", "SF-010"}
    assert request.state.internal_preview is True


def test_semifinal_preview_stays_closed_for_normal_team(tmp_path):
    access, token = controller(tmp_path, organizer=False)
    with pytest.raises(StorageError) as captured:
        access.authorize(make_request(token, "192.168.51.50"), "semifinal", "SF-003")
    assert captured.value.code == "SESSION_UNASSIGNED"


def test_semifinal_preview_rejects_organizer_from_wrong_source(tmp_path):
    access, token = controller(tmp_path, organizer=True)
    with pytest.raises(StorageError) as captured:
        access.authorize(make_request(token, "192.168.51.99"), "semifinal", "SF-003")
    assert captured.value.code == "SESSION_UNASSIGNED"
