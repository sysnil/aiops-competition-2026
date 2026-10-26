from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _expand(value: str, project_root: Path, base_url: str) -> str:
    replacements = {
        "${PROJECT_ROOT}": str(project_root),
        "${PYTHON}": sys.executable,
        "${BASE_URL}": base_url,
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    return value


class MCPToolClient:
    def __init__(self, config_path: str | Path, project_root: str | Path, base_url: str, run_dir: str | Path, mode: str = "practice", team_token: str | None = None, final_task_id: str | None = None):
        self.config_path = Path(config_path)
        self.project_root = Path(project_root)
        self.base_url = base_url
        self.run_dir = Path(run_dir)
        self.mode = mode
        self.team_token = team_token
        self.final_task_id = final_task_id
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.log_path = self.run_dir / "mcp_calls.jsonl"

    def _params(self, server: dict[str, Any]) -> StdioServerParameters:
        command = _expand(server["command"], self.project_root, self.base_url)
        args = [_expand(x, self.project_root, self.base_url) for x in server.get("args", [])]
        env = dict(os.environ)
        env.update({k: _expand(str(v), self.project_root, self.base_url) for k, v in server.get("env", {}).items()})
        env["AIOPS_MODE"] = self.mode
        if self.final_task_id:
            env["AIOPS_FINAL_TASK_ID"] = self.final_task_id
        if self.team_token:
            env["AIOPS_TEAM_TOKEN"] = self.team_token
        return StdioServerParameters(command=command, args=args, cwd=str(self.project_root), env=env)

    def _log(self, payload: dict[str, Any]) -> None:
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    async def _discover_async(self) -> list[dict[str, Any]]:
        discovered: list[dict[str, Any]] = []
        for server in self.config.get("servers", []):
            if not server.get("enabled", True):
                continue
            try:
                async with stdio_client(self._params(server)) as streams:
                    async with ClientSession(*streams) as session:
                        await session.initialize()
                        response = await session.list_tools()
                        tools = [{"name": x.name, "description": x.description} for x in response.tools]
                        discovered.append({"server": server["name"], "tools": tools, "ok": True})
            except Exception as exc:
                discovered.append({"server": server.get("name"), "tools": [], "ok": False, "error": str(exc)})
        self._log({"event": "discovery", "servers": discovered, "timestamp": time.time()})
        return discovered

    def discover(self) -> list[dict[str, Any]]:
        return asyncio.run(self._discover_async())

    async def _call_async(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        for server in self.config.get("servers", []):
            if not server.get("enabled", True):
                continue
            async with stdio_client(self._params(server)) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    if tool_name not in {x.name for x in listed.tools}:
                        continue
                    result = await session.call_tool(tool_name, arguments, read_timeout_seconds=30)
                    if bool(getattr(result, "isError", False)):
                        messages = [str(getattr(block, "text", "")) for block in result.content]
                        raise RuntimeError("; ".join(x for x in messages if x) or "MCP tool returned an error")
                    structured = getattr(result, "structuredContent", None)
                    if structured is None:
                        structured = getattr(result, "structured_content", None)
                    if isinstance(structured, dict):
                        return structured
                    for block in result.content:
                        text = getattr(block, "text", None)
                        if text:
                            try:
                                return json.loads(text)
                            except json.JSONDecodeError:
                                return {"text": text}
                    return {"is_error": bool(getattr(result, "isError", False)), "content": []}
        raise LookupError(f"MCP tool not found: {tool_name}")

    def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        try:
            result = asyncio.run(self._call_async(tool_name, arguments))
            self._log(
                {
                    "event": "call",
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": result,
                    "latency_ms": round((time.monotonic() - started) * 1000, 3),
                    "ok": True,
                }
            )
            return result
        except Exception as exc:
            failure = {"supported_conclusion": "UNKNOWN", "warnings": [f"MCP failure: {exc}"], "error": str(exc)}
            self._log(
                {
                    "event": "call",
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": failure,
                    "latency_ms": round((time.monotonic() - started) * 1000, 3),
                    "ok": False,
                }
            )
            return failure
