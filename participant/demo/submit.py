from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from jsonschema import Draft202012Validator, FormatChecker


TERMINAL = {"COMPLETED", "PREVIEW_COMPLETED", "PENDING_GT_APPROVAL", "PENDING_JUDGE", "PENDING_REVIEW", "REJECTED", "FAILED"}


def save_result(path: Path, result: dict[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def submit_and_wait(
    client: "SubmissionClient", submit_path: str, poll_path: str,
    body: dict[str, Any], output: Path, wait_seconds: float, poll_interval: float,
) -> dict[str, Any]:
    result = client.request("POST", submit_path, body)
    save_result(output, result)
    deadline = time.monotonic() + wait_seconds
    while result.get("status") not in TERMINAL and time.monotonic() < deadline:
        time.sleep(max(poll_interval, 0.2))
        result = client.request("GET", poll_path.format(submission_id=result["submission_id"]))
        save_result(output, result)
    if result.get("status") not in TERMINAL:
        result["message"] = "客户端等待超时；评测可能仍在进行，可稍后使用相同答案再次查询。"
        save_result(output, result)
    return result


def canonical_hash(answer: dict[str, Any]) -> str:
    raw = json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def load_credential(explicit: str | None, path: Path | None) -> str:
    token = (explicit or os.environ.get("AIOPS_TEAM_TOKEN", "") or os.environ.get("AIOPS_SUBMISSION_TOKEN", "")).strip()
    if token:
        return token
    candidate = path or Path("/etc/aiops-competition/team.env")
    if candidate.is_file():
        for line in candidate.read_text(encoding="utf-8").splitlines():
            if line.startswith(("AIOPS_TEAM_TOKEN=", "AIOPS_SUBMISSION_TOKEN=")):
                return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError("未配置队伍凭证；请设置 AIOPS_TEAM_TOKEN 或使用 --team-token")


class SubmissionClient:
    def __init__(self, server: str, token: str, timeout: float, retries: int, retry_log: Path | None = None):
        self.server = server.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.retry_log = retry_log
        self.client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}", "User-Agent": "aiops-submit/1.0"},
        )

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        last_error: Exception | None = None
        retry_deadline = time.monotonic() + 180.0
        for attempt in range(self.retries + 1):
            try:
                response = self.client.request(method, self.server + path, json=json_body)
                if response.status_code in {429, 502, 503, 504} and attempt < self.retries:
                    retry_after = response.headers.get("Retry-After")
                    delay = min(5.0 * (2**attempt), 30.0)
                    if retry_after:
                        try: delay = max(0.0, float(retry_after))
                        except ValueError:
                            try: delay = max(0.0, (parsedate_to_datetime(retry_after)-datetime.now(timezone.utc)).total_seconds())
                            except (ValueError,TypeError): pass
                    if time.monotonic() + delay <= retry_deadline:
                        if self.retry_log:
                            with self.retry_log.open("a",encoding="utf-8") as log:
                                log.write(json.dumps({"event":"submission_retry","method":method,"path":path,"status":response.status_code,"attempt":attempt+1,"delay_seconds":delay,"timestamp":time.time()})+"\n")
                        print(f"提交/评测接口 HTTP {response.status_code}，等待 {delay:.1f}s 后重试（{attempt+1}/{self.retries}，复用同一幂等键）。",flush=True)
                        time.sleep(delay)
                        continue
                try:
                    payload = response.json()
                except ValueError as exc:
                    preview = response.text.strip().replace("\n", " ")[:240]
                    raise RuntimeError(
                        f"提交服务返回了非 JSON 响应（HTTP {response.status_code}）：{preview or '<empty>'}"
                    ) from exc
                if response.status_code >= 400:
                    error = payload.get("error", {}) if isinstance(payload, dict) else {}
                    code = error.get("code", f"HTTP_{response.status_code}")
                    message = error.get("message", "提交服务返回错误")
                    raise RuntimeError(f"{code}: {message}")
                if not isinstance(payload, dict):
                    raise RuntimeError("提交服务返回了无效响应")
                return payload
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(0.5 * (2**attempt), 3.0))
                    continue
                raise RuntimeError(f"网络连接失败或请求超时：{type(exc).__name__}") from exc
        raise RuntimeError(f"请求失败：{last_error}")


def print_result(result: dict[str, Any]) -> None:
    # Final preview responses keep the authoritative score under ``grade``
    # while legacy semifinal responses expose it at the top level.  Normalize
    # only for display; the raw server response is still persisted unchanged.
    grade = result.get("grade") if isinstance(result.get("grade"), dict) else result
    qa = result.get("task_type") == "SEMIFINAL_QUERY_VISUALIZATION" or result.get("schema_version") == "1.0"
    semifinal = result.get("schema_version") == "3.0"
    final = result.get("task_type") == "FINAL_ANOMALY_INVESTIGATION" or result.get("schema_version") == "final-1.0" or str(result.get("task_id", "")).startswith(("FT-", "PK-"))
    print("=" * 40)
    print("院智能运维技能大赛 · 决赛 Agent 评测" if final else "院智能运维技能大赛 · 半决赛查询绘图评分" if qa else "院智能运维技能大赛 · 半决赛异常检测评分" if semifinal else "院智能运维技能大赛 · 历史 v2 自动评测")
    print("=" * 40)
    print(f"模式：{result.get('mode') or grade.get('grading_mode') or 'UNKNOWN'}")
    print(f"题目：{result.get('task_id') or result.get('question_id') or result.get('case_id')}")
    print(f"队伍：{result.get('team_id', 'UNKNOWN')}")
    print(f"Submission ID：{result.get('submission_id', 'UNKNOWN')}")
    if qa:
        print("任务：指定查询、绘图与三问作答")
    elif semifinal:
        print("任务：多模态异常检测")
    elif final:
        print("任务：决赛自主异常调查")
    display_status = grade.get("status") or result.get("status")
    print(f"状态：{display_status}")
    score = grade.get("score")
    if (display_status in {"COMPLETED", "PREVIEW_COMPLETED"}
            and isinstance(score, (int, float)) and not isinstance(score, bool)
            and math.isfinite(score)):
        breakdown = grade.get("breakdown", {})
        print()
        if qa:
            print(f"图表正确性          {breakdown.get('chart', 0):g} / 4")
            print(f"问题一              {breakdown.get('question_1', 0):g} / 2")
            print(f"问题二              {breakdown.get('question_2', 0):g} / 2")
            print(f"问题三              {breakdown.get('question_3', 0):g} / 2")
        elif semifinal:
            print(f"异常发现与现象识别    {breakdown.get('anomaly_detection', 0):g} / 40")
        elif final:
            if all(key in breakdown for key in ("fault_type", "entity", "time", "evidence")):
                print(f"故障类型识别          {breakdown.get('fault_type', 0):g} / 40")
                print(f"异常实体定位          {breakdown.get('entity', 0):g} / 20")
                print(f"异常时间定位          {breakdown.get('time', 0):g} / 20")
                print(f"监控证据质量          {breakdown.get('evidence', 0):g} / 20")
            elif breakdown:
                print(f"匹配事件            {breakdown.get('matched_events', 0)}")
                print(f"GT 事件            {breakdown.get('gt_events', 0)}")
                print(f"事件级维度明细      {len(breakdown.get('event_scores', []))} 条（详见 JSON）")
            else:
                print("评分细节暂未公开")
        if not qa and not final:
            print(f"异常实体定位          {breakdown.get('entity', 0):g} / 20")
            print(f"异常时间定位          {breakdown.get('time', 0):g} / 20")
            print(f"监控证据质量          {breakdown.get('evidence', 0):g} / 20")
        print()
        if not qa and (not final or breakdown):
            print(f"多报扣分              {grade.get('penalty', result.get('penalty', 0)):g}")
        print("-" * 40)
        if final:
            display_score = grade.get("display_score")
            if not isinstance(display_score, (int, float)) or isinstance(display_score, bool) or not math.isfinite(display_score):
                display_score = round(float(score) * 10, 2)
            print(f"本题得分            {display_score:g} / 100")
        elif qa:
            print(f"本题得分            {score:g} / 10")
        else:
            print(f"最终得分              {score:g} / 100")
    else:
        print("提交已接收；评测分数尚未生成或尚未公开。")
    if result.get("message"):
        print(f"说明：{result['message']}")
    print("=" * 40)


def main() -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="提交 AIOps 比赛答案并等待自动评测")
    parser.add_argument("--case", required=True)
    parser.add_argument("--answer", type=Path, required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--mode", choices=["practice", "semifinal", "final"], default="practice")
    parser.add_argument("--task-id", help="Final task ID, for example FT-01 or PK-R01")
    parser.add_argument("--round-id", help="Live PK round ID; when set, submit through the PK round endpoint")
    parser.add_argument("--team-token", help="队伍 Bearer Token；推荐使用 AIOPS_TEAM_TOKEN 环境变量")
    parser.add_argument("--schema", type=Path, help="可选；默认按 task_type/schema_version 选择 QA v1、异常检测 v3 或历史 v2 Schema")
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--idempotency-key")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--wait-seconds", type=float, default=90.0)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--retries", type=int, default=6)
    args = parser.parse_args()
    if args.answer.stat().st_size > 2_400_000:
        raise SystemExit("答案文件超过 2400000 字节的客户端限制")
    try:
        answer = json.loads(args.answer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取合法 JSON：{exc}")
    version = answer.get("schema_version")
    if version not in {"1.0", "2.0", "3.0", "final-1.0"}:
        raise SystemExit("仅支持查询绘图 v1、异常检测 v3、决赛 final-1.0 和历史 v2 文件")
    if answer.get("case_id") != args.case:
        raise SystemExit("--case 与答案中的 case_id 不一致")
    schema_path = args.schema or (
        root / "schemas/qa-answer-v1.schema.json" if version == "1.0" else
        root / "schemas/answer.schema.json" if version == "3.0" else
        root / "schemas/final-answer-v1.schema.json" if version == "final-1.0" else
        root / "schemas/answer-v2.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(answer), key=lambda e: list(e.path))
    if errors:
        messages = [f"{'.'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors[:10]]
        raise SystemExit("本地 Schema 校验失败：\n" + "\n".join(messages))
    answer_sha = canonical_hash(answer)
    idempotency_key = args.idempotency_key or f"answer-{answer_sha[:48]}"
    token = load_credential(args.team_token, args.credential_file)
    client = SubmissionClient(args.server, token, args.timeout, args.retries, retry_log=args.answer.parent / "submission_requests.jsonl")
    try:
        if version == "final-1.0":
            if args.mode != "final":
                raise RuntimeError("Final answer requires --mode final")
            if args.round_id:
                submit_path = f"/v1/final/pk/rounds/{args.round_id}/submissions"
            else:
                task_id = args.task_id or answer.get("task_id")
                if not task_id or task_id != answer.get("task_id"):
                    raise RuntimeError("--task-id must match answer.task_id")
                submit_path = f"/v1/final/tasks/{task_id}/submissions"
            body = {"idempotency_key": idempotency_key, "answer_sha256": answer_sha, "answer": answer}
            poll_path = "/v1/final/submissions/{submission_id}"
        else:
            submit_path = f"/v1/{args.mode}/submissions/qa" if version == "1.0" else f"/v1/{args.mode}/submissions"
            body = {
                "case_id": args.case,
                "schema_version": version,
                "idempotency_key": idempotency_key,
                "answer_sha256": answer_sha,
                "answer": answer,
            }
            poll_path = f"/v1/{args.mode}/submissions/{{submission_id}}"
        output = args.output or args.answer.parent / "grading-result.json"
        result = submit_and_wait(client, submit_path, poll_path, body, output, args.wait_seconds, args.poll_interval)
        print_result(result)
        print(f"评分结果已保存：{output}")
        return 0 if result.get("status") in {"COMPLETED", "PREVIEW_COMPLETED", "PENDING_GT_APPROVAL", "PENDING_JUDGE", "PENDING_REVIEW"} else 2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
