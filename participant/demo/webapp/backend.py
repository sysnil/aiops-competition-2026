from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import signal
import time
from collections import defaultdict
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, Query, Request as FastAPIRequest
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator


PROJECT_ROOT = Path(os.getenv("AIOPS_WEB_PROJECT_ROOT", Path(__file__).resolve().parents[1])).resolve()
STATE_ROOT = Path(os.getenv("AIOPS_WEB_STATE_ROOT", "/var/lib/aiops-agent-web")).resolve()
WEB_RUNS_ROOT = STATE_ROOT / "runs"
CLI_RUNS_ROOT = Path(os.getenv("AIOPS_WEB_CLI_RUN_ROOT", PROJECT_ROOT / "runs")).resolve()
PUBLIC_BASE_URL = os.getenv("AIOPS_PUBLIC_BASE_URL", "http://192.168.51.51:8080").rstrip("/")
PYTHON = os.getenv("AIOPS_AGENT_PYTHON", "/opt/aiops-competition/.venv/bin/python")
TEAM_ENV_FILE = Path(os.getenv("AIOPS_TEAM_ENV_FILE", "/etc/aiops-competition/team.env"))
LLM_ENV_FILE = Path(os.getenv("AIOPS_LLM_ENV_FILE", "/etc/aiops-competition/agent-llm.env"))
FINAL_STAGE2_TEAMS = tuple(
    value.strip() for value in os.getenv("AIOPS_FINAL_STAGE2_TEAMS", "").split(",") if value.strip()
)
STATIC_ROOT = Path(__file__).resolve().parent / "static"
MAX_ACTIVE_RUNS = 4
MAX_QUESTION_CHARS = 4000
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
SAFE_CASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def llm_configured() -> bool:
    values = load_env_file(LLM_ENV_FILE)
    return all(values.get(key) for key in (
        "AIOPS_AGENT_LLM_BASE_URL", "AIOPS_AGENT_LLM_MODEL", "AIOPS_AGENT_LLM_API_KEY",
    ))


def compact(value: Any, limit: int = 1000) -> Any:
    """Return a JSON-safe bounded summary; never emit model prompts or hidden reasoning."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        value = value.replace("\x00", "")
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, list):
        return [compact(item, max(120, limit // 4)) for item in value[:12]]
    if isinstance(value, dict):
        blocked = {"authorization", "api_key", "apikey", "token", "team_token", "prompt", "messages", "content"}
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:30]:
            if str(key).lower() in blocked:
                continue
            result[str(key)] = compact(item, max(120, limit // 3))
        return result
    return compact(str(value), limit)


class RunCreate(BaseModel):
    case_id: str = Field(default="SF-001", min_length=1, max_length=64)
    mode: str = Field(default="practice", pattern="^(practice|semifinal|final)$")
    agent: str = Field(default="baseline", pattern="^(baseline|llm)$")
    skills: bool = True
    mcp: bool = True
    question: str = Field(min_length=4, max_length=MAX_QUESTION_CHARS)
    stage: str = Field(default="semifinal", pattern="^(semifinal|final_qualifier|final_pk)$")
    task: str = Field(default="qa", pattern="^(qa|anomaly|final_qualifier|final_pk)$")
    question_id: str | None = Field(default="P-Q01", max_length=64)
    final_task_id: str | None = Field(default=None, max_length=64)
    request_budget: int = Field(default=120, ge=10, le=300)

    @field_validator("case_id")
    @classmethod
    def valid_case(cls, value: str) -> str:
        if not SAFE_CASE_RE.fullmatch(value):
            raise ValueError("invalid case_id")
        return value

    @field_validator("question_id")
    @classmethod
    def valid_question_id(cls, value: str | None) -> str | None:
        if value is not None and not SAFE_CASE_RE.fullmatch(value):
            raise ValueError("invalid question_id")
        return value

    @field_validator("final_task_id")
    @classmethod
    def valid_final_task_id(cls, value: str | None) -> str | None:
        if value is not None and not SAFE_CASE_RE.fullmatch(value):
            raise ValueError("invalid final_task_id")
        return value


class RunRecord:
    def __init__(self, run_id: str, run_dir: Path, config: dict[str, Any]) -> None:
        self.run_id = run_id
        self.run_dir = run_dir
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task[None] | None = None
        self.status = "STARTING"
        self.started_at = utc_now()
        self.finished_at: str | None = None
        self.return_code: int | None = None
        self.stop_requested = False


def run_task_key(config: dict[str, Any]) -> str:
    if config.get("mode") == "final" and config.get("final_task_id"):
        return f"final:{config['final_task_id']}"
    if config.get("task") == "qa":
        return f"{config.get('mode')}:{config.get('question_id')}"
    return f"{config.get('mode')}:{config.get('case_id')}:{config.get('task')}"


def ensure_run_slot(active: list[RunRecord], config: dict[str, Any]) -> None:
    task_key = run_task_key(config)
    if any(run_task_key(item.config) == task_key for item in active):
        raise HTTPException(409, detail={"code": "TASK_ALREADY_RUNNING", "message": "这道题已有 Agent 在运行。"})
    if len(active) >= MAX_ACTIVE_RUNS:
        raise HTTPException(409, detail={"code": "RUN_CAPACITY_REACHED", "message": "本机并发运行已达上限。"})


class EventStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = asyncio.Lock()
        self.seq = 0
        if path.exists():
            with suppress(Exception):
                self.seq = sum(1 for _ in path.open("r", encoding="utf-8"))

    async def append(self, event_type: str, phase: str, status: str, payload: dict[str, Any], *, source: str = "web", delivery_mode: str = "live") -> dict[str, Any]:
        async with self.lock:
            self.seq += 1
            event = {
                "seq": self.seq,
                "ts": utc_now(),
                "type": event_type,
                "phase": phase,
                "status": status,
                "source": source,
                "delivery_mode": delivery_mode,
                "payload": ({"provenance":payload.get("provenance","public-topology-api"),"nodes":payload.get("nodes",[])[:128],"edges":payload.get("edges",[])[:512]} if event_type=="topology.observed" and isinstance(payload,dict) else compact(payload,1800)),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            return event


class RunManager:
    def __init__(self) -> None:
        self.active: dict[str, RunRecord] = {}
        self._lock = asyncio.Lock()

    async def start(self, spec: RunCreate) -> dict[str, Any]:
        is_final = spec.stage in {"final_qualifier", "final_pk"}
        if spec.agent == "llm" and not llm_configured():
            raise HTTPException(422, detail={
                "code": "LLM_UNAVAILABLE",
                "message": "本机尚未配置 LLM API。请选择 Baseline，或等待组委会统一配置模型。",
            })
        if spec.task == "qa" and not spec.question_id and not is_final:
            raise HTTPException(422, detail={"code": "QUESTION_ID_REQUIRED", "message": "查询与可视化题必须选择 Question ID。"})

        # Never trust a manually crafted browser request.  Re-check the
        # selected case/question against the permission-filtered lists from
        # the organizer API before starting the local process.
        if is_final:
            task_status, task_payload = await asyncio.to_thread(organizer_get, "/v1/final/tasks")
            task_items = extract_items(task_payload)
            if task_status != 200:
                raise HTTPException(task_status if 400 <= task_status < 600 else 502, detail={"code": "FINAL_TASK_LIST_UNAVAILABLE", "message": "无法读取当前身份可见的决赛任务。"})
            expected_kind = "FT" if spec.stage == "final_qualifier" else "PK"
            selected_task = next((item for item in task_items if str(item.get("task_id")) == str(spec.final_task_id)), None)
            if selected_task is None or selected_task.get("kind") != expected_kind:
                raise HTTPException(403, detail={"code": "FINAL_TASK_NOT_AUTHORIZED", "message": "所选决赛任务不在当前 Token 的可见列表中。"})
            expected_status = "OPEN" if expected_kind == "FT" else "RUNNING"
            if selected_task.get("status") != expected_status and not task_payload.get("internal_preview"):
                raise HTTPException(409, detail={"code": "FINAL_TASK_NOT_OPEN", "message": "所选决赛任务尚未开放。"})
            spec.case_id = str(selected_task["case_id"])
            spec.mode = "final"
            spec.task = spec.stage
            topology_status, topology_payload = await asyncio.to_thread(organizer_get, f"/v1/final/tasks/{spec.final_task_id}/case/topology")
        else:
            case_status, case_payload = await asyncio.to_thread(organizer_cases, spec.mode)
            case_items = extract_items(case_payload)
            if case_status != 200:
                raise HTTPException(case_status if 400 <= case_status < 600 else 502, detail={"code": "CASE_LIST_UNAVAILABLE", "message": "无法读取当前身份可访问的 Case 列表。"})
            if not any(str(item.get("case_id")) == spec.case_id for item in case_items):
                raise HTTPException(403, detail={"code": "CASE_NOT_AUTHORIZED", "message": "所选 Case 不在当前 Token 和比赛模式的可访问列表中。"})
            topology_status, topology_payload = await asyncio.to_thread(organizer_get, f"/v1/{spec.mode}/cases/{spec.case_id}/topology")
        if spec.task == "qa" and not is_final:
            question_status, question_payload = await asyncio.to_thread(organizer_get, f"/v1/{spec.mode}/questions")
            question_items = extract_items(question_payload)
            if question_status != 200:
                raise HTTPException(question_status if 400 <= question_status < 600 else 502, detail={"code": "QUESTION_LIST_UNAVAILABLE", "message": "无法读取当前身份可访问的 Question 列表。"})
            selected_question = next((item for item in question_items if str(item.get("question_id")) == spec.question_id), None)
            if selected_question is None:
                raise HTTPException(403, detail={"code": "QUESTION_NOT_AUTHORIZED", "message": "所选 Question 不在当前 Token 和比赛模式的可访问列表中。"})
            if str(selected_question.get("case_id")) != spec.case_id:
                raise HTTPException(422, detail={"code": "QUESTION_CASE_MISMATCH", "message": "Question 与 Case 不匹配，请从动态列表重新选择。"})

        authorized_topology = normalize_topology_payload(topology_payload) if topology_status == 200 else empty_topology("unavailable")
        async with self._lock:
            active = [item for item in self.active.values()
                      if item.status in {"STARTING", "RUNNING", "STOPPING"}]
            ensure_run_slot(active, spec.model_dump())
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_id = f"web-{stamp}-{secrets.token_hex(3)}"
            run_dir = (WEB_RUNS_ROOT / run_id).resolve()
            run_dir.mkdir(parents=True, exist_ok=False)
            question_file = run_dir / "question.txt"
            question_file.write_text(spec.question.strip() + "\n", encoding="utf-8")
            config = spec.model_dump()
            meta = {"run_id": run_id, "created_at": utc_now(), "source": "web", "config": config, "internal_test":os.getenv("AIOPS_WEB_INTERNAL_TEST")=="1", "public_api_base":PUBLIC_BASE_URL}
            write_json_atomic(run_dir / "web_meta.json", meta)
            # Keep a bounded Public topology snapshot with the run so replay
            # remains complete even after the competition mode is closed.
            write_json_atomic(run_dir / "web_topology.json", authorized_topology)
            record = RunRecord(run_id, run_dir, config)
            self.active[run_id] = record
            record.task = asyncio.create_task(self._execute(record, question_file), name=f"agent-{run_id}")
            return summarize_run_dir(run_dir, run_id=run_id, active=record)

    async def _execute(self, record: RunRecord, question_file: Path) -> None:
        store = EventStore(record.run_dir / "web_events.jsonl")
        await store.append("run.started", "START", "running", {"run_id": record.run_id, "config": record.config})
        topology = normalize_topology_payload(read_json(record.run_dir / "web_topology.json", {}))
        if topology["nodes"]:
            await store.append("topology.observed", "OBSERVE", "complete", topology)
        env = os.environ.copy()
        env.update(load_env_file(TEAM_ENV_FILE))
        if "AIOPS_TEAM_TOKEN" not in env:
            await store.append("run.blocked", "AUTH", "failed", {"code": "TEAM_TOKEN_MISSING", "message": "后端未读取到 AIOPS_TEAM_TOKEN"})
            record.status = "FAILED"
            record.finished_at = utc_now()
            self._write_web_result(record)
            return
        args = [
            PYTHON,
            str(PROJECT_ROOT / "main.py"),
            "--task", record.config["task"],
            "--agent", record.config["agent"],
            "--case", record.config["case_id"],
            "--base-url", PUBLIC_BASE_URL,
            "--mode", record.config["mode"],
            "--question-file", str(question_file),
            "--run-dir", str(record.run_dir),
            "--skills-dir", str(PROJECT_ROOT / "skills"),
            "--mcp-config", str(PROJECT_ROOT / "configs" / "mcp_servers.json"),
            "--llm-env-file", str(LLM_ENV_FILE),
            "--request-budget", str(record.config["request_budget"]),
        ]
        if record.config["task"] in {"final_qualifier", "final_pk"}:
            args.extend(["--final-task", record.config["final_task_id"], "--submit"])
        elif record.config["task"] == "qa":
            # A Web QA run is expected to exercise the same end-to-end path as
            # the CLI: generate deterministic artifacts, submit them through
            # the existing gateway, and surface the server result.  Session
            # permissions remain enforced by 51; this flag does not open or
            # bypass a closed competition mode.
            args.extend(["--question", record.config["question_id"], "--submit"])
        else:
            if not record.config["skills"]:
                args.append("--no-skills")
            if not record.config["mcp"]:
                args.append("--no-mcp")
        tail_task: asyncio.Task[None] | None = None
        try:
            record.process = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            record.status = "RUNNING"
            await store.append("process.started", "DISCOVER", "running", {"pid": record.process.pid, "command": "main.py", "agent": record.config["agent"]})
            tail_task = asyncio.create_task(tail_agent_files(record, store))
            assert record.process.stdout is not None
            with (record.run_dir / "web_process.log").open("ab") as output:
                while chunk := await record.process.stdout.readline():
                    output.write(chunk)
                    output.flush()
            record.return_code = await record.process.wait()
            record.status = "STOPPED" if record.stop_requested else ("COMPLETE" if record.return_code == 0 else "FAILED")
        except Exception as exc:
            record.status = "FAILED"
            await store.append("process.error", "EXECUTE", "failed", {"error": type(exc).__name__, "message": str(exc)[:600]})
        finally:
            if tail_task:
                await asyncio.sleep(0.4)
                tail_task.cancel()
                with suppress(asyncio.CancelledError):
                    await tail_task
                await ingest_final_files(record, store)
            record.finished_at = utc_now()
            self._write_web_result(record)
            await store.append("run.finished", "COMPLETE", record.status.lower(), {"return_code": record.return_code, "status": record.status, "summary": summarize_run_dir(record.run_dir, active=record)})

    def _write_web_result(self, record: RunRecord) -> None:
        payload = {
            "run_id": record.run_id,
            "status": record.status,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "return_code": record.return_code,
            "stop_requested": record.stop_requested,
        }
        write_json_atomic(record.run_dir / "web_result.json", payload)

    async def stop(self, run_id: str) -> dict[str, Any]:
        record = self.active.get(run_id)
        if record is None or record.status not in {"STARTING", "RUNNING"}:
            raise HTTPException(409, detail={"code": "RUN_NOT_ACTIVE", "message": "该运行当前不在执行。"})
        record.stop_requested = True
        record.status = "STOPPING"
        store = EventStore(record.run_dir / "web_events.jsonl")
        await store.append("run.stop_requested", "STOP", "running", {"run_id": run_id})
        if record.process and record.process.returncode is None:
            with suppress(ProcessLookupError):
                os.killpg(record.process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(record.process.wait(), timeout=8)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(record.process.pid, signal.SIGKILL)
        return summarize_run_dir(record.run_dir, active=record)


manager = RunManager()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    except OSError:
        pass
    return rows


def extract_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def empty_topology(provenance: str = "public-api") -> dict[str, Any]:
    return {"provenance": provenance, "nodes": [], "edges": []}


def normalize_topology_payload(payload: Any) -> dict[str, Any]:
    """Convert the Public Topology API envelope to the bounded ECharts shape."""
    if not isinstance(payload, dict):
        return empty_topology()
    root = payload.get("topology") if isinstance(payload.get("topology"), dict) else payload
    nodes: set[str] = set()
    edges: set[tuple[str, str]] = set()

    raw_nodes = root.get("nodes") or root.get("services") or []
    if isinstance(raw_nodes, list):
        for item in raw_nodes:
            if isinstance(item, dict):
                name = item.get("id") or item.get("name") or item.get("service") or item.get("service_name")
            else:
                name = item
            if name:
                nodes.add(str(name))

    snapshots = root.get("snapshots") or []
    if isinstance(snapshots, list):
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                continue
            pods = snapshot.get("pods") or []
            if not isinstance(pods, list):
                continue
            for pod in pods:
                if isinstance(pod, dict) and (pod.get("service") or pod.get("service_name")):
                    nodes.add(str(pod.get("service") or pod.get("service_name")))

    raw_edges = root.get("observed_service_edges") or root.get("edges") or root.get("dependencies") or []
    if isinstance(raw_edges, list):
        for edge in raw_edges:
            if not isinstance(edge, dict):
                continue
            source = edge.get("source") or edge.get("source_service") or edge.get("parent_service") or edge.get("from")
            target = edge.get("destination") or edge.get("target") or edge.get("target_service") or edge.get("service") or edge.get("to")
            if source:
                nodes.add(str(source))
            if target:
                nodes.add(str(target))
            if source and target and str(source) != str(target):
                edges.add((str(source), str(target)))

    # Prevent an unexpected upstream payload from overwhelming a 1080p page.
    bounded_nodes = sorted(nodes)[:120]
    allowed = set(bounded_nodes)
    bounded_edges = [(source, target) for source, target in sorted(edges) if source in allowed and target in allowed][:300]
    return {
        "provenance": "public-topology-api",
        "nodes": [{"id": name, "name": name} for name in bounded_nodes],
        "edges": [{"source": source, "target": target} for source, target in bounded_edges],
    }


def actual_token_usage(run_dir: Path) -> dict[str, int] | None:
    total: defaultdict[str, int] = defaultdict(int)
    found = False
    aliases = {
        "prompt_tokens": "input_tokens", "input_tokens": "input_tokens",
        "completion_tokens": "output_tokens", "output_tokens": "output_tokens",
        "total_tokens": "total_tokens",
    }
    for row in iter_jsonl(run_dir / "llm_calls.jsonl"):
        usage = row.get("usage")
        if not isinstance(usage, dict):
            continue
        for src, dst in aliases.items():
            value = usage.get(src)
            if isinstance(value, int):
                total[dst] += value
                found = True
    if not found:
        return None
    if "total_tokens" not in total and ("input_tokens" in total or "output_tokens" in total):
        total["total_tokens"] = total["input_tokens"] + total["output_tokens"]
    return dict(total)


def derive_visuals(run_dir: Path) -> dict[str, Any]:
    answer = read_json(run_dir / "answer.json", {}) or {}
    anomalies = answer.get("anomalies", []) if isinstance(answer, dict) else []
    metrics: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    nodes: set[str] = set()
    edges: set[tuple[str, str]] = set()
    stored_topology = normalize_topology_payload(read_json(run_dir / "web_topology.json", {}))
    for node in stored_topology.get("nodes", []):
        if isinstance(node, dict) and node.get("id"):
            nodes.add(str(node["id"]))
    for edge in stored_topology.get("edges", []):
        if isinstance(edge, dict) and edge.get("source") and edge.get("target"):
            edges.add((str(edge["source"]), str(edge["target"])))

    # Tool results contain a deliberately bounded set of representative Public
    # records.  They let the Web show every observed modality without loading
    # thousands of raw rows into the browser.
    for call in iter_jsonl(run_dir / "agent_tool_calls.jsonl"):
        result = call.get("result")
        if not isinstance(result, dict):
            continue
        modality = str(result.get("modality") or "").lower()
        representatives = result.get("representative_records")
        if not isinstance(representatives, list):
            continue
        for raw in representatives[:8]:
            if not isinstance(raw, dict):
                continue
            item = compact(raw, 900)
            if modality == "metrics":
                metrics.append(item)
            elif modality == "logs":
                logs.append(item)
            elif modality == "traces":
                traces.append(item)
                service = str(raw.get("service") or raw.get("service_name") or "")
                parent = str(raw.get("parent_service") or raw.get("source_service") or "")
                if service:
                    nodes.add(service)
                if parent:
                    nodes.add(parent)
                if service and parent and service != parent:
                    edges.add((parent, service))
    for anomaly in anomalies if isinstance(anomalies, list) else []:
        if not isinstance(anomaly, dict):
            continue
        entity = str(anomaly.get("entity_id") or "unknown")
        if str(anomaly.get("entity_type","")).casefold()=="service":
            nodes.add(entity)
        for evidence in anomaly.get("evidence", []) if isinstance(anomaly.get("evidence"), list) else []:
            if not isinstance(evidence, dict):
                continue
            record = evidence.get("record") if isinstance(evidence.get("record"), dict) else {}
            kind = str(evidence.get("modality") or evidence.get("evidence_type") or evidence.get("type") or "").lower()
            item = {"entity": entity, "request_id": evidence.get("request_id"), **compact(record or evidence, 700)}
            if "metric" in kind or "metric_name" in record or "value" in record:
                metrics.append(item)
            elif "log" in kind or "message" in record or "body" in record:
                logs.append(item)
            elif "trace" in kind or "trace_id" in record or "span_id" in record:
                traces.append(item)
                # Server spans normally expose parent_service -> service.  Some
                # client-oriented evidence instead exposes source -> target.
                parent = str(record.get("parent_service") or "")
                service = str(record.get("service_name") or record.get("service") or entity)
                source = str(record.get("source_service") or parent or service)
                target = str(record.get("target_service") or record.get("peer_service") or record.get("downstream_service") or (service if parent else ""))
                nodes.add(source)
                if target:
                    nodes.add(target)
                    if source != target:
                        edges.add((source, target))
    qa = None
    if isinstance(answer, dict) and answer.get("task_type") == "SEMIFINAL_QUERY_VISUALIZATION":
        chart_data = read_json(run_dir / "chart_data.json", {}) or {}
        chart_series = chart_data.get("series", []) if isinstance(chart_data, dict) else []
        chart = answer.get("chart", {})
        qa = {
            "question_id": answer.get("question_id"),
            "answers": compact(answer.get("answers", []), 4000),
            "chart": compact({key: value for key, value in chart.items() if key != "artifact"}, 2000)
            if isinstance(chart, dict) else {},
            "source": compact(answer.get("data_source", {}), 1200),
            "chart_artifact": "chart.png" if (run_dir / "chart.png").is_file() else None,
            "series_count": len(chart_series) if isinstance(chart_series, list) else 0,
        }
    final_charts: list[dict[str, Any]] = []
    if isinstance(answer, dict) and answer.get("task_type") == "FINAL_ANOMALY_INVESTIGATION":
        for chart in answer.get("charts", []) if isinstance(answer.get("charts"), list) else []:
            if not isinstance(chart, dict):
                continue
            artifact = chart.get("artifact") if isinstance(chart.get("artifact"), dict) else {}
            filename = artifact.get("filename")
            if isinstance(filename, str) and re.fullmatch(r"final-chart-[0-9]{2}\.png", filename) and (run_dir / filename).is_file():
                final_charts.append({"title": chart.get("title"), "filename": filename, "sha256": artifact.get("sha256")})
    return {
        "anomalies": compact(anomalies, 4000),
        "metrics": metrics[:200],
        "logs": logs[:120],
        "traces": traces[:120],
        "topology": {
            "provenance": "public-topology-api" if stored_topology.get("nodes") else "evidence-derived",
            "nodes": [{"id": name, "name": name} for name in sorted(nodes)],
            "edges": [{"source": source, "target": target} for source, target in sorted(edges)],
        },
        "qa": qa,
        "final_charts": final_charts,
    }


def summarize_run_dir(run_dir: Path, *, run_id: str | None = None, active: RunRecord | None = None) -> dict[str, Any]:
    summary = read_json(run_dir / "summary.json", {}) or {}
    result = read_json(run_dir / "web_result.json", {}) or {}
    meta = read_json(run_dir / "web_meta.json", {}) or {}
    answer = read_json(run_dir / "answer.json", {}) or {}
    submission = final_submission_result(run_dir)
    stat = run_dir.stat() if run_dir.exists() else None
    rid = run_id or run_dir.name
    if active:
        status = active.status
        started_at = active.started_at
        finished_at = active.finished_at
        config = active.config
    else:
        status = result.get("status") or summary.get("status") or ("COMPLETE" if answer else "HISTORICAL")
        started_at = result.get("started_at") or meta.get("created_at")
        finished_at = result.get("finished_at")
        config = meta.get("config", {})
    raw_task = config.get("task") or summary.get("task_type") or answer.get("task_type") or "anomaly"
    is_qa = answer.get("task_type")=="SEMIFINAL_QUERY_VISUALIZATION" or raw_task in {"qa","SEMIFINAL_QUERY_VISUALIZATION"}
    task = "qa" if is_qa else raw_task
    failure_code = None
    if str(status).upper() == "FAILED":
        submit_log = run_dir / "submit.log"
        if submit_log.is_file() and not submission.get("submission_id"):
            with submit_log.open("rb") as handle:
                handle.seek(max(0, submit_log.stat().st_size - 4096))
                tail = handle.read()
            failure_code = next((code for code in (
                "SUBMISSION_LIMIT_REACHED", "SUBMISSION_CLOSED", "RATE_LIMITED",
                "INVALID_PUBLIC_EVIDENCE", "SUBMISSION_TOO_LARGE",
            ) if code.encode() in tail), "SUBMISSION_FAILED")
        elif not answer:
            process_log = run_dir / "web_process.log"
            if process_log.is_file():
                with process_log.open("rb") as handle:
                    handle.seek(max(0, process_log.stat().st_size - 4096))
                    if b"LLM_UNAVAILABLE" in handle.read():
                        failure_code = "LLM_UNAVAILABLE"
            failure_code = failure_code or "AGENT_FAILED_BEFORE_ANSWER"
    row = {
        "run_id": rid,
        "source": meta.get("source") or ("web" if meta else "cli-history"),
        "delivery_mode": "live" if active and status in {"STARTING", "RUNNING", "STOPPING"} else "replay",
        "status": str(status).upper(),
        "failure_code": failure_code,
        "started_at": started_at or (datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z") if stat else None),
        "finished_at": finished_at,
        "case_id": config.get("case_id") or summary.get("case_id") or answer.get("case_id") or "unknown",
        "agent": config.get("agent") or summary.get("agent_mode") or "unknown",
        "mode": config.get("mode") or ("final" if answer.get("task_type")=="FINAL_ANOMALY_INVESTIGATION" else "unknown"),
        "task": task,
        "question_id": config.get("question_id") or summary.get("question_id") or answer.get("question_id"),
        "final_task_id": None if is_qa else config.get("final_task_id") or summary.get("final_task_id") or answer.get("task_id"),
        "answer_count": len(answer.get("answers", [])) if isinstance(answer, dict) and isinstance(answer.get("answers"), list) else 0,
        "skills": config.get("skills"),
        "mcp": config.get("mcp"),
        "elapsed_seconds": summary.get("elapsed_seconds"),
        "api_requests": summary.get("request_count") or len(iter_jsonl(run_dir / "api_requests.jsonl")),
        "llm_calls": summary.get("llm_calls") or len(iter_jsonl(run_dir / "llm_calls.jsonl")),
        "mcp_calls": summary.get("mcp_calls") or max(0, len(iter_jsonl(run_dir / "mcp_calls.jsonl")) - 1),
        "skill_executions": summary.get("skill_executions"),
        "anomaly_count": summary.get("anomaly_count") if summary.get("anomaly_count") is not None else len(answer.get("anomalies", [])) if isinstance(answer, dict) else 0,
        "evidence_valid": summary.get("evidence_valid"),
        "model": summary.get("model"),
        "token_usage": actual_token_usage(run_dir),
        "submission_status": submission.get("status"),
        "grading_status": (submission.get("grade") or {}).get("status") if isinstance(submission.get("grade"), dict) else None,
        "submission_id": submission.get("submission_id"),
        "score": submission_score(submission),
    }
    row["score_source"] = "SERVER_CURRENT_GRADE" if row.get("score") is not None else None
    row["rubric_version"] = (submission.get("grade") or {}).get("rubric_version")
    saved_task=(read_json(run_dir/"final_task.json",{}) or {}).get("task",{})
    row["public_task_snapshot"] = {key:saved_task.get(key) for key in ("task_id","case_id","kind","status","title","description","public_record_counts","query_start_utc","query_end_utc")} if isinstance(saved_task,dict) and saved_task.get("task_id")==row["final_task_id"] and saved_task.get("case_id")==row["case_id"] else None
    task_doc=read_json(run_dir/"final_task.json",{}) or {}
    row["grading_mode"] = submission.get("grading_mode") or (submission.get("grade") or {}).get("grading_mode")
    row["internal_preview"] = bool(task_doc.get("internal_preview") or row.get("submission_status")=="PREVIEW_COMPLETED" or row["grading_mode"] in {"PREVIEW","INTERNAL_PREVIEW","HYBRID_INTERNAL_PREVIEW"} or meta.get("internal_test") is True or (read_json(run_dir/"test_scope.json",{}) or {}).get("scope")=="ISOLATED_TEST_ONLY" or os.getenv("AIOPS_WEB_INTERNAL_TEST")=="1")
    public_topology=normalize_topology_payload(read_json(run_dir/"web_topology.json",{}))
    row["public_topology_counts"]={"nodes":len(public_topology.get("nodes",[])),"edges":len(public_topology.get("edges",[]))} if public_topology.get("nodes") else None
    row["team_id"] = submission.get("team_id")
    row["execution_elapsed_seconds"] = None
    if started_at and finished_at:
        with suppress(ValueError,TypeError):
            row["execution_elapsed_seconds"] = max(0,(datetime.fromisoformat(finished_at.replace("Z","+00:00"))-datetime.fromisoformat(started_at.replace("Z","+00:00"))).total_seconds())
    row["answer_present"] = bool(answer)
    row["final_answer_ready"] = answer.get("task_type")=="FINAL_ANOMALY_INVESTIGATION"
    row["selection_mode"] = summary.get("selection_mode")
    row["llm_status"] = summary.get("llm_status")
    return row


def submission_score(submission: dict[str, Any]) -> int | float | None:
    grade = submission.get("grade")
    status = grade.get("status") if isinstance(grade, dict) else submission.get("status")
    if status not in {"COMPLETED", "PREVIEW_COMPLETED"}:
        return None
    value = grade.get("score") if isinstance(grade, dict) else submission.get("score")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def grading_feedback_path(run_dir: Path) -> Path:
    if run_dir.is_relative_to(WEB_RUNS_ROOT):
        return run_dir / "grading_feedback.json"
    return WEB_RUNS_ROOT / ".grading_feedback" / f"{run_dir.name}.json"


def final_submission_result(run_dir: Path) -> dict[str, Any]:
    original = read_json(run_dir / "submission.json", {}) or {}
    if not isinstance(original, dict):
        return {}
    feedback = read_json(grading_feedback_path(run_dir), {}) or {}
    if (isinstance(feedback, dict) and original.get("submission_id")
            and feedback.get("submission_id") == original.get("submission_id")
            and feedback.get("task_id") == original.get("task_id")):
        return feedback
    return original


def refresh_final_grade(run_dir: Path) -> dict[str, Any]:
    run = summarize_run_dir(run_dir)
    submission_id = run.get("submission_id")
    if (not run.get("final_task_id") or not isinstance(submission_id, str)
            or not re.fullmatch(r"FSUB-[A-Z0-9]+", submission_id)
):
        return run
    cached = read_json(grading_feedback_path(run_dir), {}) or {}
    if cached.get("submission_id") == submission_id and isinstance(cached.get("fetched_at_epoch"), (int, float)) and time.time()-cached["fetched_at_epoch"] < 30:
        return run
    status, payload = organizer_get(f"/v1/final/submissions/{submission_id}")
    if status != 200 or not isinstance(payload, dict):
        return run
    if (payload.get("submission_id") != submission_id
            or payload.get("task_id") != run["final_task_id"]
            or payload.get("case_id") != run["case_id"]):
        return run
    configured_team = load_env_file(TEAM_ENV_FILE).get("AIOPS_TEAM_ID")
    if configured_team and payload.get("team_id") != configured_team:
        return run
    feedback = {key: payload.get(key) for key in ("submission_id", "task_id", "case_id", "status")}
    grade = payload.get("grade")
    if isinstance(grade, dict):
        feedback["grade"] = {key: grade.get(key) for key in ("status", "rubric_version", "grader_version", "grading_mode", "review_status")}
        feedback["grade"]["score"] = submission_score({"grade": grade})
    feedback["grading_mode"] = payload.get("grading_mode") or (grade or {}).get("grading_mode")
    feedback["team_id"] = payload.get("team_id")
    feedback["fetched_at_epoch"] = time.time()
    path = grading_feedback_path(run_dir)
    if read_json(path, {}) != feedback:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(path, feedback)
    return summarize_run_dir(run_dir)


def locate_run(run_id: str) -> Path:
    if not RUN_ID_RE.fullmatch(run_id):
        raise HTTPException(404, "run not found")
    for root in (WEB_RUNS_ROOT, CLI_RUNS_ROOT):
        candidate = (root / run_id).resolve()
        if candidate.parent == root.resolve() and candidate.is_dir():
            return candidate
    raise HTTPException(404, "run not found")


def run_paths(source: str = "all") -> list[Path]:
    found: list[tuple[float, Path]] = []
    roots = (WEB_RUNS_ROOT,) if source == "web" else (WEB_RUNS_ROOT, CLI_RUNS_ROOT)
    for root in roots:
        if not root.is_dir():
            continue
        try:
            items = list(root.iterdir())
        except PermissionError:
            continue
        for item in items:
            try:
                eligible = item.is_dir() and ((item / "answer.json").is_file() or (item / "web_meta.json").is_file())
                if eligible:
                    found.append((item.stat().st_mtime, item))
            except PermissionError:
                continue
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [path for _, path in found]


def list_runs(limit: int = 40, offset: int = 0, source: str = "all") -> list[dict[str, Any]]:
    return [summarize_run_dir(path, active=manager.active.get(path.name))
            for path in run_paths(source)[offset:offset + limit]]


def active_runs() -> list[dict[str, Any]]:
    return [summarize_run_dir(record.run_dir, active=record)
            for record in manager.active.values()
            if record.status in {"STARTING", "RUNNING", "STOPPING"}]


def source_event(row: dict[str, Any], event_type: str, phase: str, status: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"event_type": event_type, "phase": phase, "status": status, "payload": compact(payload, 1500)}


async def tail_agent_files(record: RunRecord, store: EventStore) -> None:
    offsets: dict[str, int] = defaultdict(int)
    mappings = {
        "api_requests.jsonl": ("api.request", "OBSERVE"),
        "agent_tool_calls.jsonl": ("tool.call", "ACT"),
        "llm_calls.jsonl": ("llm.call", "PLAN"),
        "mcp_calls.jsonl": ("mcp.call", "VERIFY"),
        "submission_requests.jsonl": ("submission.request", "SUBMIT"),
    }
    while True:
        for filename, (event_type, phase) in mappings.items():
            path = record.run_dir / filename
            rows = iter_jsonl(path)
            for row in rows[offsets[filename]:]:
                status = str(row.get("status") or row.get("event") or "observed")
                if filename == "llm_calls.jsonl":
                    payload = {key: row.get(key) for key in ("purpose", "call_index", "attempt", "model", "status", "latency_ms", "finish_reason", "selected_tools", "usage")}
                elif filename == "api_requests.jsonl":
                    payload = {key: row.get(key) for key in ("request_id", "method", "path", "params", "attempt", "status", "latency_ms", "item_count", "retryable")}
                else:
                    payload = row
                await store.append(event_type, phase, status, payload, source="agent")
            offsets[filename] = len(rows)
        skill_path = record.run_dir / "skill_usage.json"
        if skill_path.exists() and offsets["skill_usage.json"] == 0:
            value = read_json(skill_path, {}) or {}
            await store.append("skill.lifecycle", "ACT", "observed", value, source="agent")
            offsets["skill_usage.json"] = 1
        await asyncio.sleep(0.35)


async def ingest_final_files(record: RunRecord, store: EventStore) -> None:
    skill_path = record.run_dir / "skill_usage.json"
    if skill_path.exists():
        await store.append("skill.summary", "VERIFY", "complete", read_json(skill_path, {}), source="agent")
    validation = read_json(record.run_dir / "evidence_validation.json")
    if validation is not None:
        await store.append("evidence.validation", "VERIFY", "complete", validation, source="agent")
    answer = read_json(record.run_dir / "answer.json")
    if answer is not None:
        await store.append("diagnosis.final", "ANSWER", "complete", {"answer": answer, "visuals": derive_visuals(record.run_dir)}, source="agent")


def historical_events(run_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seq = 0
    def add(event_type: str, phase: str, status: str, payload: Any, ts: str | None = None) -> None:
        nonlocal seq
        seq += 1
        events.append({"seq": seq, "ts": ts if ts is not None else utc_now(), "type": event_type, "phase": phase, "status": status, "source": "historical-agent", "delivery_mode": "replay", "payload": compact(payload, 1800)})
    add("run.replay_started", "START", "replay", summarize_run_dir(run_dir))
    sources = [
        ("api_requests.jsonl", "api.request", "OBSERVE"),
        ("agent_tool_calls.jsonl", "tool.call", "ACT"),
        ("llm_calls.jsonl", "llm.call", "PLAN"),
        ("mcp_calls.jsonl", "mcp.call", "VERIFY"),
        ("submission_requests.jsonl", "submission.request", "SUBMIT"),
    ]
    indexed: list[tuple[str, str, str, dict[str, Any]]] = []
    for filename, event_type, phase in sources:
        for row in iter_jsonl(run_dir / filename):
            raw_ts=row.get("timestamp") or row.get("ts")
            try:
                if isinstance(raw_ts,(int,float)) or (isinstance(raw_ts,str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?",raw_ts)):
                    epoch=float(raw_ts);ts=datetime.fromtimestamp(epoch/1000 if epoch>1e12 else epoch,timezone.utc).isoformat()
                else:ts=datetime.fromisoformat(str(raw_ts).replace("Z","+00:00")).astimezone(timezone.utc).isoformat()
            except (ValueError,TypeError,OverflowError):ts=""
            if filename == "llm_calls.jsonl":
                row = {key: row.get(key) for key in ("purpose", "call_index", "attempt", "model", "status", "latency_ms", "finish_reason", "selected_tools", "usage")}
            indexed.append((ts, event_type, phase, row))
    indexed.sort(key=lambda item: item[0])
    for ts, event_type, phase, row in indexed:
        add(event_type, phase, str(row.get("status") or row.get("event") or "observed"), row, ts)
    skill = read_json(run_dir / "skill_usage.json")
    if skill is not None:
        add("skill.summary", "VERIFY", "complete", skill)
    validation = read_json(run_dir / "evidence_validation.json")
    if validation is not None:
        add("evidence.validation", "VERIFY", "complete", validation)
    answer = read_json(run_dir / "answer.json")
    if answer is not None:
        add("diagnosis.final", "ANSWER", "complete", {"answer": answer, "visuals": derive_visuals(run_dir)})
    add("run.finished", "COMPLETE", "complete", summarize_run_dir(run_dir))
    return events


def live_events(run_dir: Path) -> list[dict[str, Any]]:
    return iter_jsonl(run_dir / "web_events.jsonl")


async def event_stream(run_id: str, after: int, replay: bool, speed: float, request: FastAPIRequest) -> AsyncIterator[str]:
    run_dir = locate_run(run_id)
    active = manager.active.get(run_id)
    if replay or (not (run_dir / "web_events.jsonl").exists()
                  and (active is None or active.status not in {"STARTING", "RUNNING", "STOPPING"})):
        rows = live_events(run_dir) if (run_dir/"web_events.jsonl").exists() else []
        if not rows:rows = historical_events(run_dir)
        for row in rows:
            if int(row.get("seq", 0)) <= after:
                continue
            if await request.is_disconnected():
                return
            yield f"id: {row['seq']}\nevent: agent_event\ndata: {json.dumps(row, ensure_ascii=False, separators=(',', ':'))}\n\n"
            await asyncio.sleep(max(0.02, 0.45 / speed))
        yield "event: replay_complete\ndata: {}\n\n"
        return
    last = after
    idle = 0
    while not await request.is_disconnected():
        rows = live_events(run_dir)
        new_rows = [row for row in rows if int(row.get("seq", 0)) > last]
        for row in new_rows:
            last = int(row.get("seq", last))
            yield f"id: {last}\nevent: agent_event\ndata: {json.dumps(row, ensure_ascii=False, separators=(',', ':'))}\n\n"
        state = manager.active.get(run_id)
        if state is None or state.status not in {"STARTING", "RUNNING", "STOPPING"}:
            if not new_rows:
                idle += 1
            if idle >= 3:
                yield "event: run_complete\ndata: {}\n\n"
                return
        else:
            idle = 0
        if not new_rows:
            yield ": keepalive\n\n"
        await asyncio.sleep(0.75)


def organizer_get(path: str) -> tuple[int, Any]:
    env = load_env_file(TEAM_ENV_FILE)
    token = env.get("AIOPS_TEAM_TOKEN") or os.getenv("AIOPS_TEAM_TOKEN")
    if not token:
        return 503, {"code": "TEAM_TOKEN_MISSING", "message": "后端未配置队伍 Token"}
    url = f"{PUBLIC_BASE_URL}{path}"
    request = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "aiops-visualization-web/1.0"})
    try:
        with urlopen(request, timeout=8) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        with suppress(Exception):
            body = json.loads(exc.read().decode("utf-8"))
            return exc.code, compact(body, 600)
        return exc.code, {"code": "UPSTREAM_HTTP_ERROR", "message": f"组委会 API 返回 HTTP {exc.code}"}
    except (URLError, TimeoutError, json.JSONDecodeError):
        return 502, {"code": "UPSTREAM_UNAVAILABLE", "message": "无法连接组委会 Public API"}


def organizer_cases(mode: str) -> tuple[int, Any]:
    return organizer_get(f"/v1/{mode}/cases")


@asynccontextmanager
async def lifespan(_: FastAPI):
    WEB_RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    directory_lock=(WEB_RUNS_ROOT.parent/"web-process.lock").open("a")
    try:
        fcntl.flock(directory_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        directory_lock.close()
        raise RuntimeError("Another Web process owns this state directory; use an independent AIOPS_WEB_STATE_ROOT")
    try:
        for run_dir in WEB_RUNS_ROOT.iterdir():
            if (run_dir.is_dir() and (run_dir / "web_meta.json").is_file()
                    and not (run_dir / "web_result.json").exists()):
                meta = read_json(run_dir / "web_meta.json", {}) or {}
                write_json_atomic(run_dir / "web_result.json", {
                    "run_id": run_dir.name, "status": "INTERRUPTED",
                    "started_at": meta.get("created_at"), "finished_at": utc_now(),
                    "return_code": None, "stop_requested": False,
                    "reason": "WEB_SERVICE_RESTART",
                })
                await EventStore(run_dir / "web_events.jsonl").append(
                    "run.interrupted", "STOP", "failed", {"reason":"WEB_SERVICE_RESTART"})
        yield
    finally:
        for record in list(manager.active.values()):
            if record.status in {"STARTING","RUNNING","STOPPING"} and record.process:
                with suppress(ProcessLookupError):os.killpg(record.process.pid,signal.SIGTERM)
        directory_lock.close()


app = FastAPI(title="AIOps Agent Visualization Web", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/assets", StaticFiles(directory=STATIC_ROOT), name="assets")


@app.middleware("http")
async def security_headers(request: FastAPIRequest, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'"
    return response


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok", "time": utc_now(), "service": "aiops-agent-web",
        "public_api": PUBLIC_BASE_URL, "team_token_configured": bool(load_env_file(TEAM_ENV_FILE).get("AIOPS_TEAM_TOKEN")),
        "llm_configured": llm_configured(),
        "final_round_api": "permission_gated",
    }


@app.get("/api/cases")
async def cases(mode: str = Query("practice", pattern="^(practice|semifinal)$")) -> JSONResponse:
    status, data = await asyncio.to_thread(organizer_cases, mode)
    return JSONResponse(status_code=status, content={"upstream_status": status, "data": data})


@app.get("/api/questions")
async def questions(mode: str = Query("practice", pattern="^(practice|semifinal)$")) -> JSONResponse:
    status, data = await asyncio.to_thread(organizer_get, f"/v1/{mode}/questions")
    return JSONResponse(status_code=status, content={"upstream_status": status, "data": data})


@app.get("/api/topology")
async def topology(case_id: str = Query(..., min_length=1, max_length=64), mode: str = Query("practice", pattern="^(practice|semifinal)$")) -> JSONResponse:
    if not SAFE_CASE_RE.fullmatch(case_id):
        raise HTTPException(422, detail={"code": "INVALID_CASE_ID", "message": "Case ID 格式不正确。"})
    status, data = await asyncio.to_thread(organizer_get, f"/v1/{mode}/cases/{case_id}/topology")
    content = normalize_topology_payload(data) if status == 200 else compact(data, 800)
    return JSONResponse(status_code=status, content={"upstream_status": status, "case_id": case_id, "data": content})


@app.get("/api/final/current")
async def final_current() -> JSONResponse:
    pk_status, pk_data = await asyncio.to_thread(organizer_get, "/v1/final/pk/rounds/current")
    round_status, round_data = await asyncio.to_thread(organizer_get, "/v1/final/rounds/current")
    return JSONResponse(content={
        "upstream_status": 200 if 200 in {pk_status, round_status} else max(pk_status, round_status),
        "pk": {"status": pk_status, "data": pk_data},
        "round": {"status": round_status, "data": round_data},
    })


@app.get("/api/final/tasks")
async def final_tasks() -> JSONResponse:
    status, data = await asyncio.to_thread(organizer_get, "/v1/final/tasks")
    return JSONResponse(status_code=status, content={"upstream_status": status, "data": data})


@app.get("/api/final/stage2-status")
async def final_stage2_status() -> dict[str, Any]:
    """Expose only public/internal-preview score summaries; never read private GT."""
    if not FINAL_STAGE2_TEAMS:
        return {"enabled": False, "teams": {}, "rounds": []}
    teams: dict[str, Any] = {}
    for team_id in FINAL_STAGE2_TEAMS:
        if not SAFE_CASE_RE.fullmatch(team_id):
            continue
        status, data = await asyncio.to_thread(
            organizer_get, f"/v1/final/qualification/summary?team_id={team_id}"
        )
        teams[team_id] = {"status": status, "data": data}
    rounds: list[dict[str, Any]] = []
    for number in range(1, 9):
        round_id = f"PK-R{number:02d}"
        status, data = await asyncio.to_thread(organizer_get, f"/v1/final/pk/rounds/{round_id}")
        rounds.append({"round_id": round_id, "status": status, "data": data})
    batch_ids = {
        item["data"].get("batch_id") for item in teams.values()
        if item.get("status") == 200 and isinstance(item.get("data"), dict) and item["data"].get("batch_id")
    }
    return {
        "enabled": True,
        "batch_id": next(iter(batch_ids), None),
        "classification": "INTERNAL_PREVIEW",
        "teams": teams,
        "rounds": rounds,
    }


@app.get("/api/final/qualification")
async def final_qualification() -> JSONResponse:
    status, data = await asyncio.to_thread(organizer_get, "/v1/final/qualification/summary")
    return JSONResponse(status_code=status, content={"upstream_status": status, "data": data})


@app.get("/api/final/round")
async def final_round(round_id: str = Query(..., pattern=r"^PK-R[0-9]{2}$")) -> JSONResponse:
    status, data = await asyncio.to_thread(organizer_get, f"/v1/final/pk/rounds/{round_id}")
    return JSONResponse(status_code=status, content={"upstream_status": status, "data": data})


@app.get("/api/final/topology")
async def final_topology(task_id: str = Query(..., min_length=1, max_length=64)) -> JSONResponse:
    if not SAFE_CASE_RE.fullmatch(task_id):
        raise HTTPException(422, detail={"code": "INVALID_TASK_ID", "message": "Task ID 格式不正确。"})
    status, data = await asyncio.to_thread(organizer_get, f"/v1/final/tasks/{task_id}/case/topology")
    content = normalize_topology_payload(data) if status == 200 else compact(data, 800)
    return JSONResponse(status_code=status, content={"upstream_status": status, "task_id": task_id, "data": content})


@app.get("/api/runs")
async def runs(limit: int = Query(30, ge=1, le=100),
               offset: int = Query(0, ge=0),
               source: str = Query("all", pattern="^(all|web)$")) -> dict[str, Any]:
    paths = run_paths(source)
    return {"items": [summarize_run_dir(path, active=manager.active.get(path.name))
                      for path in paths[offset:offset + limit]],
            "total": len(paths), "offset": offset, "limit": limit}


@app.get("/api/runs/active")
async def current_run() -> dict[str, Any]:
    items = active_runs()
    return {"runs": items, "run": items[-1] if items else None,
            "max_active_runs": MAX_ACTIVE_RUNS}


@app.post("/api/runs", status_code=202)
async def create_run(spec: RunCreate) -> dict[str, Any]:
    return await manager.start(spec)


@app.get("/api/runs/{run_id}")
async def run_detail(run_id: str) -> dict[str, Any]:
    run_dir = locate_run(run_id)
    run = summarize_run_dir(run_dir, active=manager.active.get(run_id))
    visuals = derive_visuals(run_dir)
    topology = visuals.get("topology") if isinstance(visuals.get("topology"), dict) else empty_topology()
    # Older runs predate the persisted topology snapshot.  Enrich those
    # replays from the same permission-gated Public API when it is available.
    if topology.get("provenance") != "public-topology-api" and run.get("mode") in {"practice", "semifinal"} and run.get("case_id") not in {None, "unknown"}:
        status, data = await asyncio.to_thread(organizer_get, f"/v1/{run['mode']}/cases/{run['case_id']}/topology")
        if status == 200:
            public_topology = normalize_topology_payload(data)
            if public_topology["nodes"]:
                visuals["topology"] = public_topology
    elif topology.get("provenance") != "public-topology-api" and run.get("mode") == "final" and run.get("final_task_id"):
        status, data = await asyncio.to_thread(organizer_get, f"/v1/final/tasks/{run['final_task_id']}/case/topology")
        if status == 200:
            public_topology = normalize_topology_payload(data)
            if public_topology["nodes"]:
                visuals["topology"] = public_topology
    return {"run": run, "visuals": visuals}


# Read-only visualization queries are separate from Agent inference and answers.
_TRACE_VIEW_CACHE: dict[tuple, dict[str, Any]] = {}


@app.get("/api/runs/{run_id}/trace/{trace_id}")
async def run_trace_detail(run_id: str, trace_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-fA-F]{1,32}", trace_id):
        raise HTTPException(404, detail={"code":"TRACE_NOT_IN_RUN","message":"Trace 不属于本次公开观测"})
    run_dir=locate_run(run_id)
    observed=derive_visuals(run_dir)
    if trace_id not in {str(r.get("trace_id")) for r in observed.get("traces",[]) if isinstance(r,dict)}:
        raise HTTPException(404, detail={"code":"TRACE_NOT_IN_RUN","message":"Trace 不属于本次公开观测"})
    run=summarize_run_dir(run_dir)
    key=(run_id,trace_id,PUBLIC_BASE_URL,TEAM_ENV_FILE.stat().st_mtime_ns if TEAM_ENV_FILE.exists() else None)
    if key in _TRACE_VIEW_CACHE:return _TRACE_VIEW_CACHE[key]
    archive_path=run_dir/"visualization-traces"/(trace_id+".json")
    saved=read_json(archive_path,{}) if archive_path.is_file() and not archive_path.is_symlink() else {}
    if (saved.get("trace_id")==trace_id and saved.get("case_id")==run.get("case_id")
            and isinstance(saved.get("spans"),list)
            and saved.get("records_sha256")==hashlib.sha256(json.dumps(saved["spans"],sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()):
        return saved
    if run.get("final_task_id"):
        path=f"/v1/final/tasks/{run['final_task_id']}/case/traces/{trace_id}"
    elif run.get("mode") in {"practice","semifinal"}:
        path=f"/v1/{run['mode']}/cases/{run['case_id']}/traces/{trace_id}"
    else:raise HTTPException(409,detail={"code":"TRACE_SOURCE_UNAVAILABLE","message":"当前运行缺少可验证的数据来源"})
    metadata_path=path.split("/traces/")[0]+"/metadata"
    meta_status,metadata=await asyncio.to_thread(organizer_get,metadata_path)
    if meta_status!=200 or not isinstance(metadata,dict) or metadata.get("case_id")!=run.get("case_id"):
        raise HTTPException(meta_status if 400<=meta_status<600 else 409,detail={"code":"TRACE_CASE_BINDING_CHANGED","message":"当前任务绑定与该历史记录不同；仅展示已归档证据"})
    status,payload=await asyncio.to_thread(organizer_get,path)
    if status!=200:
        raise HTTPException(status if 400<=status<600 else 502,detail={"code":"PUBLIC_TRACE_UNAVAILABLE","message":"公开 Trace 接口暂不可用"})
    if not isinstance(payload,dict) or payload.get("trace_id") not in (None,trace_id):
        raise HTTPException(502,detail={"code":"TRACE_ID_MISMATCH","message":"公开 Trace 身份校验失败"})
    spans=payload.get("spans",payload.get("items",[]))
    if not isinstance(spans,list) or any(not isinstance(r,dict) or r.get("trace_id") not in (None,trace_id) for r in spans):
        raise HTTPException(502,detail={"code":"TRACE_ID_MISMATCH","message":"公开 Trace 身份校验失败"})
    result={"trace_id":trace_id,"spans":spans[:1000],"truncated":True if len(spans)>1000 else payload.get("truncated"),"source":"AUTHORIZED_PUBLIC_TRACE_DETAIL_API","visualization_query":True}
    result.update(case_id=run["case_id"],task_id=run.get("final_task_id"),source_base_url=PUBLIC_BASE_URL,records_sha256=hashlib.sha256(json.dumps(result["spans"],sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest())
    archive_path.parent.mkdir(mode=0o750,exist_ok=True)
    write_json_atomic(archive_path,result)
    if len(_TRACE_VIEW_CACHE)>=32:_TRACE_VIEW_CACHE.pop(next(iter(_TRACE_VIEW_CACHE)))
    _TRACE_VIEW_CACHE[key]=result
    return result


@app.get("/api/runs/{run_id}/grade")
async def run_grade(run_id: str) -> dict[str, Any]:
    run_dir = locate_run(run_id)
    return {"run": await asyncio.to_thread(refresh_final_grade, run_dir)}


@app.get("/api/runs/{run_id}/artifacts/{name}")
async def run_artifact(run_id: str, name: str) -> FileResponse:
    allowed = {"chart.png", "answer.json", "chart_data.json", "summary.json", "submission.json"}
    if name not in allowed and not re.fullmatch(r"final-chart-[0-9]{2}\.png", name):
        raise HTTPException(404, "artifact not found")
    path = locate_run(run_id) / name
    if not path.is_file():
        raise HTTPException(404, "artifact not found")
    return FileResponse(path)


@app.post("/api/runs/{run_id}/stop", status_code=202)
async def stop_run(run_id: str) -> dict[str, Any]:
    return await manager.stop(run_id)


@app.get("/api/runs/{run_id}/events")
async def run_events(request: FastAPIRequest, run_id: str, after: int = Query(0, ge=0), replay: bool = False, speed: float = Query(1.0, ge=0.25, le=8.0)) -> StreamingResponse:
    locate_run(run_id)
    last_event_id = request.headers.get("last-event-id", "")
    if last_event_id.isdigit():
        after = max(after, int(last_event_id))
    return StreamingResponse(event_stream(run_id, after, replay, speed, request), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.exception_handler(HTTPException)
async def http_exception_handler(_: FastAPIRequest, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
