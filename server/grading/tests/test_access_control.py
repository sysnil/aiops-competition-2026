from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.access_control import AccessController
from app.security import derive_hash
from app.storage import StorageError


class FakeRequest:
    def __init__(self, token: str, source_ip: str | None = None):
        self.headers = {"authorization": f"Bearer {token}"}
        expected = {"TOKEN01": "192.168.51.52", "TOKEN02": "192.168.51.53", "TOKEN03": "192.168.51.54"}
        token_id = token.split(".")[1] if "." in token else ""
        self.state = SimpleNamespace(source_ip=expected.get(token_id, "") if source_ip is None else source_ip)


class AccessControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.registry = root / "registry.json"
        self.state = root / "state.json"
        salt = "01" * 16
        secret = "secret-value"
        self.token = f"team.TOKEN01.{secret}"
        self.pm_token = f"team.TOKEN02.{secret}"
        self.unassigned_token = f"team.TOKEN03.{secret}"
        self.registry.write_text(json.dumps({"teams": [{
            "team_id": "TEAM-01", "team_name": "one", "assigned_ip": "192.168.51.52",
            "session": "AM", "token_id": "TOKEN01", "token_status": "ACTIVE",
            "allowed_modes": ["PRACTICE", "SEMIFINAL"], "token_salt": salt,
            "token_hash": derive_hash(secret, salt),
        }, {
            "team_id": "TEAM-02", "team_name": "two", "assigned_ip": "192.168.51.53",
            "session": "PM", "token_id": "TOKEN02", "token_status": "ACTIVE",
            "allowed_modes": ["PRACTICE", "SEMIFINAL"], "token_salt": salt,
            "token_hash": derive_hash(secret, salt),
        }, {
            "team_id": "TEAM-03", "team_name": "three", "assigned_ip": "192.168.51.54",
            "session": "UNASSIGNED", "token_id": "TOKEN03", "token_status": "ACTIVE",
            "allowed_modes": ["PRACTICE", "SEMIFINAL"], "token_salt": salt,
            "token_hash": derive_hash(secret, salt),
        }, {
            "team_id": "TEAM-04", "team_name": "four", "assigned_ip": "192.168.51.55",
            "session": "AM", "token_id": "TOKEN04", "token_status": "REVOKED",
            "allowed_modes": ["PRACTICE", "SEMIFINAL"], "token_salt": salt,
            "token_hash": derive_hash(secret, salt),
        }]}), encoding="utf-8")
        self.state.write_text(json.dumps({
            "practice": {"state": "OPEN", "published_cases": ["SF-001"]},
            "semifinal": {"published_cases": ["SF-003"], "sessions": {"AM": {"state": "OPEN"}, "PM": {"state": "NOT_OPEN"}}},
        }), encoding="utf-8")
        self.access = AccessController(self.registry, self.state)

    def tearDown(self):
        self.temp.cleanup()

    def test_practice_and_assigned_open_session_are_authorized(self):
        self.assertEqual(self.access.authorize(FakeRequest(self.token), "practice", "SF-001").team_id, "TEAM-01")
        self.assertEqual(self.access.authorize(FakeRequest(self.token), "semifinal", "SF-003").session, "AM")

    def test_case_mode_and_unpublished_cases_do_not_leak(self):
        for mode, case_id in (("practice", "SF-003"), ("semifinal", "SF-004")):
            with self.assertRaises(StorageError) as caught:
                self.access.authorize(FakeRequest(self.token), mode, case_id)
            self.assertEqual((caught.exception.code, caught.exception.status_code), ("CASE_NOT_FOUND", 404))

    def test_missing_wrong_and_revoked_tokens_are_rejected(self):
        for token in ("bad", "team.TOKEN01.wrong", "team.TOKEN04.secret-value"):
            with self.assertRaises(StorageError) as caught:
                self.access.authenticate(FakeRequest(token))
            self.assertEqual(caught.exception.status_code, 401)

    def test_x_team_id_cannot_change_identity(self):
        request = FakeRequest(self.token)
        request.headers["x-team-id"] = "TEAM-17"
        self.assertEqual(self.access.authenticate(request).team_id, "TEAM-01")

    def test_team_token_is_bound_to_registered_source_ip(self):
        for source_ip in ("192.168.51.53", ""):
            with self.assertRaises(StorageError) as caught:
                self.access.authorize(FakeRequest(self.token, source_ip), "practice", "SF-001")
            self.assertEqual((caught.exception.code, caught.exception.status_code), ("SOURCE_IP_MISMATCH", 403))

    def test_unassigned_and_other_session_are_blocked(self):
        for token, code in ((self.unassigned_token, "SESSION_UNASSIGNED"), (self.pm_token, "SESSION_NOT_OPEN")):
            with self.assertRaises(StorageError) as caught:
                self.access.authorize(FakeRequest(token), "semifinal", "SF-003")
            self.assertEqual(caught.exception.code, code)

    def test_session_switch_is_rechecked_for_each_request(self):
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["semifinal"]["sessions"]["AM"]["state"] = "CLOSED"
        state["semifinal"]["sessions"]["PM"]["state"] = "OPEN"
        self.state.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(StorageError):
            self.access.authorize(FakeRequest(self.token), "semifinal", "SF-003")
        self.assertEqual(self.access.authorize(FakeRequest(self.pm_token), "semifinal", "SF-003").team_id, "TEAM-02")
