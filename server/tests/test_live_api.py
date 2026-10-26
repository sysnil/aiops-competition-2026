from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest


ENDPOINT = os.getenv("AIOPS_TEST_ENDPOINT", "http://192.168.51.51:8080")
MANIFEST = Path(os.getenv("AIOPS_TEST_MANIFEST", "/data/aiops-contest/public/manifests/SF-001.json"))
TEAM_TOKEN = os.getenv("AIOPS_TEST_TEAM_TOKEN", "").strip()
HEADERS = (
    {"Authorization": f"Bearer {TEAM_TOKEN}"}
    if TEAM_TOKEN
    else {"X-Team-ID": "pytest-live"}
)


def get(path: str, **params):
    return httpx.get(ENDPOINT + path, headers=HEADERS, params=params, timeout=15.0)


def require_team_token() -> None:
    if not TEAM_TOKEN:
        pytest.skip("set AIOPS_TEST_TEAM_TOKEN to run authenticated live-data assertions")


def test_health_and_authentication_boundary():
    assert httpx.get(ENDPOINT + "/healthz").status_code == 200
    response = get("/v1/cases")
    if not TEAM_TOKEN:
        assert response.status_code == 401
        return
    assert response.status_code == 200
    assert all(item["classification"] == "PUBLIC_COMPETITION_DATA" for item in response.json()["items"])


def test_case_metadata_has_no_private_fields():
    require_team_token()
    response = get("/v1/cases/SF-001/metadata")
    assert response.status_code == 200
    rendered = json.dumps(response.json()).lower()
    for marker in ("ground_truth", "fault_injection", "private_answer", "/data/"):
        assert marker not in rendered


def test_all_modalities_and_stable_pagination():
    require_team_token()
    first = get("/v1/cases/SF-001/metrics", page=1, page_size=10)
    again = get("/v1/cases/SF-001/metrics", page=1, page_size=10)
    assert first.status_code == 200 and first.json() == again.json()
    assert get("/v1/cases/SF-001/logs", keyword="error", page_size=10).json()["items"]
    traces = get("/v1/cases/SF-001/traces", service="checkout", page_size=10).json()["items"]
    assert traces
    detail = get(f"/v1/cases/SF-001/traces/{traces[0]['trace_id']}")
    assert detail.status_code == 200 and detail.json()["spans"]
    assert get("/v1/cases/SF-001/topology").status_code == 200


def test_security_boundaries_and_outside_window():
    require_team_token()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert get("/v1/cases/NOT-PUBLISHED/metadata").status_code == 404
    outside = get(
        "/v1/cases/SF-001/metrics",
        start="2026-10-15T00:00:00Z",
        end="2026-10-15T00:01:00Z",
    )
    assert outside.status_code == 200 and outside.json()["items"] == []
    injection = get("/v1/cases/SF-001/logs", service="' OR 1=1 --")
    assert injection.status_code == 200 and injection.json()["items"] == []
    assert get("/v1/cases/SF-001/logs", page_size=100000).status_code in {400, 422}
    assert manifest["classification"] == "PUBLIC_COMPETITION_DATA"
