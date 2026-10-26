from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class LLMUnavailable(RuntimeError):
    pass


def load_env_file(path: str | Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if path:
        candidate = Path(path)
        if candidate.is_file():
            for raw in candidate.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("'\"")
    for key in (
        "AIOPS_AGENT_LLM_BASE_URL",
        "AIOPS_AGENT_LLM_MODEL",
        "AIOPS_AGENT_LLM_API_KEY",
        "AIOPS_AGENT_LLM_TEMPERATURE",
    ):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


@dataclass(frozen=True)
class AgentLLMConfig:
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float = 60.0
    max_output_tokens: int = 1200
    temperature: float = 0.0
    max_retries: int = 2

    @classmethod
    def load(cls, path: str | Path | None, config: dict[str, Any]) -> "AgentLLMConfig":
        values = load_env_file(path)
        base_url = values.get("AIOPS_AGENT_LLM_BASE_URL", "").rstrip("/")
        model = values.get("AIOPS_AGENT_LLM_MODEL", "")
        api_key = values.get("AIOPS_AGENT_LLM_API_KEY", "")
        if not base_url or not model or not api_key:
            raise LLMUnavailable(
                "LLM_UNAVAILABLE: configure AIOPS_AGENT_LLM_BASE_URL, "
                "AIOPS_AGENT_LLM_MODEL and AIOPS_AGENT_LLM_API_KEY"
            )
        return cls(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=float(config.get("timeout_seconds", 60)),
            max_output_tokens=int(config.get("max_output_tokens", 1200)),
            temperature=float(values.get("AIOPS_AGENT_LLM_TEMPERATURE", config.get("temperature", 0))),
            max_retries=int(config.get("max_retries", 2)),
        )


class OpenAICompatibleLLM:
    def __init__(self, config: AgentLLMConfig, run_dir: str | Path):
        self.config = config
        self.run_dir = Path(run_dir)
        self.log_path = self.run_dir / "llm_calls.jsonl"
        self.client = httpx.Client(
            timeout=config.timeout_seconds,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "aiops-participant-llm-agent/1.0",
            },
        )
        self.call_count = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    def close(self) -> None:
        self.client.close()

    @property
    def endpoint(self) -> str:
        if self.config.base_url.endswith("/chat/completions"):
            return self.config.base_url
        return self.config.base_url + "/chat/completions"

    def _log(self, payload: dict[str, Any]) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        purpose: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        max_tokens: int | None = None,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": max_tokens or self.config.max_output_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice or "auto"
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise LLMUnavailable("LLM_UNAVAILABLE: total Agent deadline exceeded")
            started = time.monotonic()
            self.call_count += 1
            try:
                request_timeout = self.config.timeout_seconds if remaining is None else min(self.config.timeout_seconds, max(1.0, remaining))
                response = self.client.post(self.endpoint, json=body, timeout=request_timeout)
                elapsed_ms = round((time.monotonic() - started) * 1000, 3)
                if response.status_code in {429, 502, 503, 504} and attempt < self.config.max_retries:
                    time.sleep(min(0.5 * (2**attempt), 3.0))
                    continue
                response.raise_for_status()
                payload = response.json()
                message = payload["choices"][0]["message"]
                usage = payload.get("usage") or {}
                self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
                self.completion_tokens += int(usage.get("completion_tokens") or 0)
                self.total_tokens += int(usage.get("total_tokens") or 0)
                content = message.get("content") or ""
                calls = message.get("tool_calls") or []
                self._log(
                    {
                        "event": "llm_call",
                        "purpose": purpose,
                        "call_index": self.call_count,
                        "attempt": attempt + 1,
                        "model": self.config.model,
                        "status": response.status_code,
                        "latency_ms": elapsed_ms,
                        "finish_reason": payload["choices"][0].get("finish_reason"),
                        "message_count": len(messages),
                        "offered_tools": [x["function"]["name"] for x in (tools or [])],
                        "selected_tools": [x.get("function", {}).get("name") for x in calls],
                        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                        "content_preview": content[:300],
                        "usage": usage,
                    }
                )
                return {
                    "message": message,
                    "finish_reason": payload["choices"][0].get("finish_reason"),
                    "usage": usage,
                }
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                last_error = exc
                self._log(
                    {
                        "event": "llm_error",
                        "purpose": purpose,
                        "call_index": self.call_count,
                        "attempt": attempt + 1,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                        "latency_ms": round((time.monotonic() - started) * 1000, 3),
                    }
                )
                if attempt < self.config.max_retries:
                    time.sleep(min(0.5 * (2**attempt), 3.0))
                    continue
        raise LLMUnavailable(f"LLM_UNAVAILABLE: {type(last_error).__name__}: {last_error}")

    def probe_tool_calling(self, deadline: float | None = None) -> bool:
        probe = {
            "type": "function",
            "function": {
                "name": "capability_probe",
                "description": "Confirm native tool calling support.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string", "enum": ["ok"]}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        }
        result = self.chat(
            [
                {"role": "system", "content": "Call capability_probe exactly once."},
                {"role": "user", "content": "Use the provided tool now."},
            ],
            purpose="tool_calling_probe",
            tools=[probe],
            tool_choice="auto",
            max_tokens=200,
            deadline=deadline,
        )
        calls = result["message"].get("tool_calls") or []
        return len(calls) == 1 and calls[0].get("function", {}).get("name") == "capability_probe"

    def usage_summary(self) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "llm_calls": self.call_count,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost": None,
            "cost_note": "Provider pricing was not supplied; token usage is recorded for later calculation.",
        }
