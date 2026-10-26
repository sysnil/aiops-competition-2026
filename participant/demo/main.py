from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from agent.core import AgentOptions, CompetitionAgent
from agent.llm_client import LLMUnavailable


def load_team_token(explicit: str | None, credential_file: Path) -> str:
    token = (explicit or os.environ.get("AIOPS_TEAM_TOKEN", "")).strip()
    if token:
        return token
    if credential_file.is_file():
        for raw in credential_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("AIOPS_TEAM_TOKEN="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parent
    result = argparse.ArgumentParser(description="AIOps competition evidence-first starter agent")
    result.add_argument("--task", choices=["anomaly", "qa", "final_qualifier", "final_pk"], default="anomaly", help="qa solves semifinal questions; final_qualifier/final_pk run the autonomous Final Agent")
    result.add_argument("--agent", choices=["baseline", "llm"], default="llm", help="baseline is deterministic; llm uses Plan-Act-Observe-Verify")
    result.add_argument("--case", default="SF-001")
    result.add_argument("--question", dest="question_id", help="QA question ID such as P-Q01 or Q01; use 'all' for every visible question")
    result.add_argument("--final-task", dest="final_task_id", help="Final task ID such as FT-01 or PK-R01; omitted only when exactly one matching OPEN task is visible")
    result.add_argument("--base-url", required=True)
    result.add_argument("--mode", choices=["practice", "semifinal", "final"], default="practice", help="competition API namespace; Final tasks still require --task final_* and a server-authorized task")
    result.add_argument("--team-token", help="team Bearer token; prefer AIOPS_TEAM_TOKEN to avoid process-list exposure")
    result.add_argument("--credential-file", type=Path, default=Path("/etc/aiops-competition/team.env"), help="pre-provisioned team credential file")
    result.add_argument("--question-file", type=Path, default=root / "examples/question.txt")
    result.add_argument("--run-dir", type=Path, help="output directory; default is a new timestamped directory under runs/")
    result.add_argument("--config", type=Path, default=root / "config.yaml")
    result.add_argument("--skills-dir", type=Path, default=root / "skills")
    result.add_argument("--mcp-config", type=Path, default=root / "configs/mcp_servers.json")
    result.add_argument("--no-skills", action="store_true", help="disable Skill discovery and execution")
    result.add_argument("--no-mcp", action="store_true", help="disable MCP discovery and execution")
    result.add_argument("--llm-env-file", type=Path, default=Path("/etc/aiops-competition/agent-llm.env"))
    result.add_argument("--request-budget", type=int, default=120)
    result.add_argument(
        "--qa-request-budget",
        type=int,
        default=220,
        help="QA mode per-question request budget; sized for fully paginating the largest frozen question",
    )
    result.add_argument("--team-id", default="starter-agent")
    result.add_argument("--submit", action="store_true", help="submit the generated QA or Final answer and wait for server feedback")
    result.add_argument("--wait-seconds", type=int, default=90, help="QA submission scoring wait timeout")
    return result


def final_task_active_status(kind: str) -> str:
    if kind == "FT":
        return "OPEN"
    if kind == "PK":
        return "RUNNING"
    raise ValueError(f"unsupported Final task kind: {kind}")


def discover_final_task(args, team_token: str, run_dir: Path) -> dict:
    from client.public_api import PublicAPI

    expected_kind = "FT" if args.task == "final_qualifier" else "PK"
    expected_status = final_task_active_status(expected_kind)
    api = PublicAPI(
        args.base_url,
        run_dir,
        args.team_id,
        mode="final",
        final_task_id=args.final_task_id,
        team_token=team_token,
        request_budget=12,
    )
    try:
        listing = api.get("/v1/final/tasks").payload
        items = [item for item in listing.get("items", []) if isinstance(item, dict) and item.get("kind") == expected_kind]
        if args.final_task_id:
            selected = next((item for item in items if item.get("task_id") == args.final_task_id), None)
            if selected is None:
                raise SystemExit(f"FINAL_TASK_NOT_AUTHORIZED: {args.final_task_id} is not visible for this identity")
        else:
            opened = [item for item in items if item.get("status") == expected_status]
            if len(opened) != 1:
                visible = ", ".join(str(item.get("task_id")) for item in items) or "none"
                raise SystemExit(f"FINAL_TASK_SELECTION_REQUIRED: use --final-task; visible {expected_kind} tasks: {visible}")
            selected = opened[0]
        if selected.get("status") != expected_status and not listing.get("internal_preview"):
            raise SystemExit(f"FINAL_TASK_NOT_OPEN: {selected.get('task_id')}")
        detail = api.get(f"/v1/final/tasks/{selected['task_id']}").payload
        task = detail.get("task") if isinstance(detail.get("task"), dict) else selected
        if task.get("case_id") != selected.get("case_id") or task.get("kind") != expected_kind:
            raise SystemExit("FINAL_TASK_METADATA_MISMATCH")
        envelope = {
            "task": task,
            "internal_preview": bool(listing.get("internal_preview") and detail.get("internal_preview")),
            "discovered_from": "/v1/final/tasks",
        }
        (run_dir / "final_task.json").write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        return envelope
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        code = "PUBLIC_API_HTTP_ERROR"
        message = "比赛 API 拒绝了请求"
        try:
            body = exc.response.json()
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                code = str(error.get("code") or code)
                message = str(error.get("message") or message)
        except (ValueError, TypeError):
            pass
        raise SystemExit(f"{code}: {message} (HTTP {status})") from None
    finally:
        api.close()


def llm_qa_plan(question: dict, args, run_dir: Path) -> dict:
    """Ask the participant LLM to interpret a public QA task before execution.

    Numeric calculations remain deterministic Python.  This planning call is
    intentionally advisory and contains only the public Question API payload;
    it never receives GT, tolerances, reference images, or hidden data.
    """
    from agent.llm_client import AgentLLMConfig, OpenAICompatibleLLM

    llm = OpenAICompatibleLLM(AgentLLMConfig.load(args.llm_env_file, {}), run_dir)
    try:
        result = llm.chat([
            {
                "role": "system",
                "content": (
                    "You are planning a deterministic observability query-and-visualization task. "
                    "Use only the public question. Identify the data modality, entities, filters, exact "
                    "left-closed/right-open window, bucket origin, missing-sample rule, percentile or "
                    "counter rule, and a short execution plan. Do not invent answers or values."
                ),
            },
            {"role": "user", "content": json.dumps(question, ensure_ascii=False)},
        ], purpose="semifinal_qa_public_plan", max_tokens=700)
        plan = {
            "model": llm.config.model,
            "content": result["message"].get("content") or "",
            "usage": result.get("usage") or None,
            "role": "public-question planning; deterministic Python remains authoritative",
        }
        (run_dir / "llm_qa_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        return llm.usage_summary()
    finally:
        llm.close()


def main() -> None:
    args = parser().parse_args()
    team_token = load_team_token(args.team_token, args.credential_file)
    if not team_token:
        raise SystemExit("AIOPS_AUTH_REQUIRED: 请加载 AIOPS_TEAM_TOKEN 或检查 /etc/aiops-competition/team.env")
    root = Path(__file__).resolve().parent
    if args.task == "qa":
        if not args.question_id:
            raise SystemExit("QA_TASK_REQUIRES_QUESTION: 请使用 --question Q01 或 --question all")
        from client.public_api import PublicAPI
        from qa_solver import QuestionSolver

        base_run_dir = args.run_dir or root / "runs" / datetime.now(timezone.utc).strftime("qa-%Y%m%dT%H%M%SZ")
        if args.question_id == "all":
            discovery = PublicAPI(args.base_url, base_run_dir / "_discovery", args.team_id, mode=args.mode, team_token=team_token, request_budget=10)
            try:
                payload = discovery.get(discovery.competition_path("questions")).payload
                question_ids = [str(item["question_id"]) for item in payload.get("items", [])]
            finally:
                discovery.close()
            if not question_ids:
                raise SystemExit("NO_VISIBLE_QUESTIONS: 当前场次没有对本队开放的题目")
        else:
            question_ids = [args.question_id]
        outputs: list[dict[str, object]] = []
        for question_id in question_ids:
            run_dir = base_run_dir / question_id if len(question_ids) > 1 else base_run_dir
            api = PublicAPI(
                args.base_url,
                run_dir,
                args.team_id,
                mode=args.mode,
                team_token=team_token,
                request_budget=args.qa_request_budget,
            )
            try:
                llm_usage = None
                if args.agent == "llm":
                    public_question = api.get(api.competition_path(f"questions/{question_id}")).payload
                    llm_usage = llm_qa_plan(public_question, args, run_dir)
                answer = QuestionSolver(api, run_dir).run(question_id)
            finally:
                api.close()
            item = {"question_id": question_id, "case_id": answer["case_id"], "answer": str(run_dir / "answer.json"), "chart": str(run_dir / "chart.png")}
            if llm_usage is not None:
                item["llm_usage"] = llm_usage
            if args.submit:
                output_path = run_dir / "submission.json"
                environment = os.environ.copy()
                environment["AIOPS_TEAM_TOKEN"] = team_token
                command = [
                    sys.executable, str(root / "submit.py"),
                    "--case", str(answer["case_id"]),
                    "--answer", str(run_dir / "answer.json"),
                    "--server", args.base_url,
                    "--mode", args.mode,
                    "--credential-file", str(args.credential_file),
                    "--output", str(output_path),
                    "--wait-seconds", str(args.wait_seconds),
                ]
                completed = subprocess.run(command, env=environment, text=True, capture_output=True)
                (run_dir / "submit.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
                if completed.returncode != 0:
                    raise RuntimeError(f"QA_SUBMISSION_FAILED: {question_id}; see {run_dir / 'submit.log'}")
                submission = json.loads(output_path.read_text(encoding="utf-8"))
                item["submission"] = str(output_path)
                item["submission_status"] = submission.get("status")
                item["score"] = submission.get("score")
            outputs.append(item)
        print(json.dumps({"task_type": "SEMIFINAL_QUERY_VISUALIZATION", "runs": outputs}, ensure_ascii=False, indent=2))
        return
    if args.task in {"final_qualifier", "final_pk"}:
        from final_answer import build_final_answer

        run_dir = args.run_dir or root / "runs" / datetime.now(timezone.utc).strftime(f"{args.task}-%Y%m%dT%H%M%SZ")
        run_dir.mkdir(parents=True, exist_ok=True)
        envelope = discover_final_task(args, team_token, run_dir)
        task = envelope["task"]
        options = AgentOptions(
            case_id=str(task["case_id"]),
            base_url=args.base_url,
            question=str(task.get("prompt") or "Investigate the Final task using public observability evidence."),
            run_dir=run_dir,
            project_root=root,
            config_path=args.config,
            skills_dir=None if args.no_skills else args.skills_dir,
            mcp_config=None if args.no_mcp else args.mcp_config,
            request_budget=args.request_budget,
            team_id=args.team_id,
            agent_mode=args.agent,
            llm_env_file=args.llm_env_file,
            competition_mode="final",
            team_token=team_token,
            final_task_id=str(task["task_id"]),
        )
        try:
            if args.agent == "llm":
                from agent.llm_core import LLMCompetitionAgent

                intermediate = LLMCompetitionAgent(options).run()
            else:
                intermediate = CompetitionAgent(options).run()
        except LLMUnavailable as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from exc
        shutil.copyfile(run_dir / "answer.json", run_dir / "anomaly-answer-v3.json")
        answer = build_final_answer(intermediate, task=task, run_dir=run_dir, project_root=root)
        output: dict[str, object] = {
            "task_type": "FINAL_ANOMALY_INVESTIGATION",
            "final_mode": args.task,
            "task_id": task["task_id"],
            "case_id": task["case_id"],
            "internal_preview": envelope["internal_preview"],
            "answer": str(run_dir / "answer.json"),
            "charts": [chart["artifact"]["filename"] for chart in answer.get("charts", [])],
        }
        if args.submit:
            output_path = run_dir / "submission.json"
            environment = os.environ.copy()
            environment["AIOPS_TEAM_TOKEN"] = team_token
            command = [
                sys.executable,
                str(root / "submit.py"),
                "--case", str(task["case_id"]),
                "--answer", str(run_dir / "answer.json"),
                "--server", args.base_url,
                "--mode", "final",
                "--task-id", str(task["task_id"]),
                "--credential-file", str(args.credential_file),
                "--output", str(output_path),
                "--wait-seconds", str(args.wait_seconds),
            ]
            completed = subprocess.run(command, env=environment, text=True, capture_output=True)
            (run_dir / "submit.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
            if completed.returncode != 0:
                raise RuntimeError(f"FINAL_SUBMISSION_FAILED: {task['task_id']}; see {run_dir / 'submit.log'}")
            submission = json.loads(output_path.read_text(encoding="utf-8"))
            grade = submission.get("grade") if isinstance(submission.get("grade"), dict) else {}
            raw_score = grade.get("score", submission.get("score"))
            display_score = grade.get("display_score")
            if (display_score is None and isinstance(raw_score, (int, float))
                    and not isinstance(raw_score, bool) and math.isfinite(raw_score)):
                display_score = round(raw_score * 10, 2)
            output.update(
                {
                    "submission": str(output_path),
                    "submission_id": submission.get("submission_id"),
                    "submission_status": submission.get("status"),
                    "score": raw_score,
                    "display_score": display_score,
                    "display_max_score": 100,
                }
            )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    question = args.question_file.read_text(encoding="utf-8").strip()
    run_dir = args.run_dir or root / "runs" / datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")
    options = AgentOptions(
        case_id=args.case,
        base_url=args.base_url,
        question=question,
        run_dir=run_dir,
        project_root=root,
        config_path=args.config,
        skills_dir=None if args.no_skills else args.skills_dir,
        mcp_config=None if args.no_mcp else args.mcp_config,
        request_budget=args.request_budget,
        team_id=args.team_id,
        agent_mode=args.agent,
        llm_env_file=args.llm_env_file,
        competition_mode=args.mode,
        team_token=team_token,
    )
    try:
        if args.agent == "llm":
            from agent.llm_core import LLMCompetitionAgent

            answer = LLMCompetitionAgent(options).run()
        else:
            answer = CompetitionAgent(options).run()
    except LLMUnavailable as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(answer, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
