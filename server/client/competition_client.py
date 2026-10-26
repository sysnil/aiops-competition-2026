from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx


class CompetitionAPIError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str, request_id: str | None = None):
        super().__init__(f"{status_code} {code}: {message}")
        self.status_code = status_code
        self.code = code
        self.request_id = request_id


class CompetitionClient:
    def __init__(
        self,
        endpoint: str,
        token: str | None = None,
        timeout: float = 10.0,
        team_id: str | None = None,
    ):
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if team_id:
            headers["X-Team-ID"] = team_id
        self._client = httpx.Client(
            base_url=endpoint.rstrip("/"),
            headers=headers,
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    @staticmethod
    def _time(value: datetime | str | None) -> str | None:
        return value.isoformat() if isinstance(value, datetime) else value

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._client.get(path, params={k: v for k, v in (params or {}).items() if v is not None})
        if response.is_error:
            try:
                detail = response.json()["error"]
            except Exception:
                response.raise_for_status()
            raise CompetitionAPIError(
                response.status_code,
                detail.get("code", "UNKNOWN"),
                detail.get("message", "request failed"),
                detail.get("request_id"),
            )
        return response.json()

    def list_cases(self) -> dict[str, Any]:
        return self._get("/v1/cases")

    def get_metadata(self, case_id: str) -> dict[str, Any]:
        return self._get(f"/v1/cases/{case_id}/metadata")

    def get_topology(self, case_id: str) -> dict[str, Any]:
        return self._get(f"/v1/cases/{case_id}/topology")

    def get_metrics(
        self,
        case_id: str,
        *,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        metric_name: str | None = None,
        service: str | None = None,
        pod: str | None = None,
        node: str | None = None,
        aggregation: str = "raw",
        step_seconds: int = 60,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        return self._get(
            f"/v1/cases/{case_id}/metrics",
            {
                "start": self._time(start), "end": self._time(end), "metric_name": metric_name,
                "service": service, "pod": pod, "node": node, "aggregation": aggregation,
                "step_seconds": step_seconds, "page": page, "page_size": page_size,
            },
        )

    def get_logs(self, case_id: str, **params: Any) -> dict[str, Any]:
        return self._get(f"/v1/cases/{case_id}/logs", params)

    def get_traces(self, case_id: str, **params: Any) -> dict[str, Any]:
        return self._get(f"/v1/cases/{case_id}/traces", params)

    def get_trace(self, case_id: str, trace_id: str) -> dict[str, Any]:
        return self._get(f"/v1/cases/{case_id}/traces/{trace_id}")
