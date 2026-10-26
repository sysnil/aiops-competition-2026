from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .settings import GradingSettings


class GraderUnavailable(RuntimeError):
    pass


class PrivateGraderClient:
    def __init__(self, settings: GradingSettings):
        self.settings = settings

    async def grade(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.grader_token:
            raise GraderUnavailable("private grader service token is not configured")
        last_error: Exception | None = None
        for attempt in range(self.settings.grader_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.settings.grader_timeout_seconds) as client:
                    response = await client.post(
                        f"{self.settings.grader_url}/internal/v1/grade",
                        headers={"Authorization": f"Bearer {self.settings.grader_token}"},
                        json=payload,
                    )
                if response.status_code in {429, 502, 503, 504} and attempt < self.settings.grader_retries:
                    await asyncio.sleep(min(0.5 * (2**attempt), 2.0))
                    continue
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict) or "status" not in result:
                    raise ValueError("private grader returned an invalid response")
                return result
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < self.settings.grader_retries:
                    await asyncio.sleep(min(0.5 * (2**attempt), 2.0))
                    continue
        raise GraderUnavailable(str(last_error))

