from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class RequestBudgetExceeded(RuntimeError):
    pass


@dataclass
class APIResult:
    request_id: str
    path: str
    params: dict[str, Any]
    payload: dict[str, Any]


class PublicAPI:
    """Bounded, observable, read-only Public API client."""

    def __init__(
        self,
        base_url: str,
        run_dir: str | Path,
        team_id: str,
        mode: str = "practice",
        final_task_id: str | None = None,
        team_token: str | None = None,
        request_budget: int = 55,
        timeout: float = 8.0,
        max_retries: int = 8,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if mode not in {"practice", "semifinal", "final"}:
            raise ValueError("mode must be practice, semifinal, or final")
        self.mode = mode
        self.final_task_id = final_task_id
        self.api_prefix = f"/v1/{mode}"
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.run_dir / "api_requests.jsonl"
        self.request_budget = request_budget
        self.timeout = timeout
        self.max_retries = max_retries
        self.request_count = 0
        headers = {"User-Agent": "aiops-participant-starter/2.0"}
        if team_token:
            headers["Authorization"] = f"Bearer {team_token}"
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers=headers,
            transport=transport,
        )

    def competition_path(self, suffix: str) -> str:
        if self.mode == "final":
            clean = suffix.lstrip("/")
            if clean == "cases":
                return "/v1/final/tasks"
            if not self.final_task_id:
                raise ValueError("final_task_id is required for Final case queries")
            prefix = "cases/"
            if not clean.startswith(prefix) or "/" not in clean[len(prefix):]:
                raise ValueError(f"unsupported Final competition path: {suffix}")
            _, resource = clean[len(prefix):].split("/", 1)
            return f"/v1/final/tasks/{self.final_task_id}/case/{resource}"
        return f"{self.api_prefix}/{suffix.lstrip('/')}"

    def close(self) -> None:
        self.client.close()

    def _write_log(self, event: dict[str, Any]) -> None:
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def get(self, path: str, params: dict[str, Any] | None = None) -> APIResult:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if self.request_count >= self.request_budget:
                raise RequestBudgetExceeded(
                    f"request budget exhausted ({self.request_count}/{self.request_budget})"
                )
            self.request_count += 1
            request_id = f"req-{self.request_count:04d}"
            started = time.monotonic()
            try:
                response = self.client.get(path, params=params)
                elapsed_ms = round((time.monotonic() - started) * 1000, 3)
                retryable = response.status_code in {429, 502, 503, 504}
                body: Any
                try:
                    body = response.json()
                except Exception:
                    body = {"raw": response.text[:1000]}
                item_count = len(body.get("items", [])) if isinstance(body, dict) else None
                self._write_log(
                    {
                        "request_id": request_id,
                        "method": "GET",
                        "path": path,
                        "params": params,
                        "attempt": attempt + 1,
                        "status": response.status_code,
                        "latency_ms": elapsed_ms,
                        "item_count": item_count,
                        "retryable": retryable,
                        "timestamp": time.time(),
                    }
                )
                if retryable and attempt < self.max_retries:
                    retry_after = response.headers.get("Retry-After")
                    # A per-minute competition limit may require waiting for a
                    # full sliding-window reset.  Short sub-second retries only
                    # amplify 429s, so use a bounded 5/10/20/30s schedule when
                    # the gateway does not provide Retry-After.
                    delay = min(float(retry_after or 5.0 * (2**attempt)), 30.0)
                    self._write_log({
                        "request_id": request_id,
                        "event": "retry_wait",
                        "status": response.status_code,
                        "delay_seconds": delay,
                        "timestamp": time.time(),
                    })
                    time.sleep(delay + random.uniform(0, 0.05))
                    continue
                response.raise_for_status()
                if not isinstance(body, dict):
                    raise ValueError(f"expected JSON object from {path}")
                return APIResult(request_id, path, params, body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                elapsed_ms = round((time.monotonic() - started) * 1000, 3)
                self._write_log(
                    {
                        "request_id": request_id,
                        "method": "GET",
                        "path": path,
                        "params": params,
                        "attempt": attempt + 1,
                        "status": None,
                        "latency_ms": elapsed_ms,
                        "error": type(exc).__name__,
                        "timestamp": time.time(),
                    }
                )
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(0.25 * (2**attempt), 2.0))
                    continue
                raise
        raise RuntimeError(f"request failed: {last_error}")

    def paged(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        max_pages: int = 1,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            page_params = dict(params or {})
            page_params.update({"page": page, "page_size": page_size})
            result = self.get(path, page_params)
            items = result.payload.get("items", [])
            if not isinstance(items, list):
                raise ValueError(f"invalid items payload from {path}")
            for item in items:
                if isinstance(item, dict):
                    item = dict(item)
                    item["_request_id"] = result.request_id
                    rows.append(item)
            if len(items) < page_size:
                break
        return rows

    @staticmethod
    def record_digest(record: dict[str, Any]) -> str:
        clean = {k: v for k, v in record.items() if not k.startswith("_")}
        raw = json.dumps(clean, ensure_ascii=False, sort_keys=True, default=str).encode()
        return hashlib.sha256(raw).hexdigest()
