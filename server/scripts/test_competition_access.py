#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import time
from pathlib import Path

import httpx


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * ratio), len(ordered) - 1)] if ordered else 0.0


async def main_async(args) -> dict:
    credentials = json.loads(args.credentials.read_text(encoding="utf-8"))["credentials"]
    teams = [item for item in credentials if item["team_id"].startswith("TEAM-")]
    paths = [
        "/v1/practice/cases",
        "/v1/practice/cases/SF-001/metadata",
        "/v1/practice/cases/SF-001/topology",
        "/v1/practice/cases/SF-001/metrics?page=1&page_size=2",
        "/v1/practice/cases/SF-001/logs?page=1&page_size=2",
        "/v1/practice/cases/SF-001/traces?page=1&page_size=2",
        "/v1/practice/cases/SF-001/traces/2bff764e6c56635e2707eba0d883a488",
    ]

    async def query(index: int, item: dict) -> dict:
        started = time.perf_counter()
        async with httpx.AsyncClient(base_url=args.base_url, timeout=15, headers={"Authorization": f"Bearer {item['token']}"}) as client:
            response = await client.get(paths[index % len(paths)], headers={"X-Team-ID": "TEAM-17"})
        return {"team_id": item["team_id"], "status": response.status_code, "latency_ms": round((time.perf_counter() - started) * 1000, 3)}

    query_results = await asyncio.gather(*(query(index, item) for index, item in enumerate(teams)))
    answer = {"case_id": "SF-001", "schema_version": "3.0", "task_type": "SEMIFINAL_ANOMALY_DETECTION", "anomalies": []}
    raw = json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()

    submit_slots = asyncio.Semaphore(2)

    async def submit(item: dict) -> dict:
        headers = {"Authorization": f"Bearer {item['token']}", "X-Team-ID": "TEAM-17"}
        envelope = {"case_id": "SF-001", "schema_version": "3.0", "idempotency_key": f"access-load-{item['team_id'].lower()}-{digest[:24]}", "answer_sha256": digest, "answer": answer}
        async with submit_slots, httpx.AsyncClient(base_url=args.base_url, timeout=20, headers=headers) as client:
            response = await client.post("/v1/practice/submissions", json=envelope)
            try:
                payload = response.json()
            except ValueError:
                payload = {"error": {"code": f"HTTP_{response.status_code}", "message": response.text[:120]}}
            if response.status_code != 202:
                return {"team_id": item["team_id"], "post_status": response.status_code, "result": payload}
            submission_id = payload["submission_id"]
            deadline = time.monotonic() + 45
            while payload.get("status") not in {"COMPLETED", "PREVIEW_COMPLETED", "PENDING_JUDGE", "PENDING_GT_APPROVAL", "REJECTED", "FAILED"} and time.monotonic() < deadline:
                await asyncio.sleep(0.25)
                polled = await client.get(f"/v1/practice/submissions/{submission_id}")
                payload = polled.json()
            return {"team_id": item["team_id"], "post_status": response.status_code, "submission_id": submission_id, "status": payload.get("status"), "response_team_id": payload.get("team_id")}

    submissions = await asyncio.gather(*(submit(item) for item in teams))
    first_sid = next((item.get("submission_id") for item in submissions if item.get("submission_id")), None)
    cross_team_status = None
    if first_sid and len(teams) > 1:
        async with httpx.AsyncClient(base_url=args.base_url, timeout=10, headers={"Authorization": f"Bearer {teams[1]['token']}"}) as client:
            cross_team_status = (await client.get(f"/v1/practice/submissions/{first_sid}")).status_code
    async with httpx.AsyncClient(base_url=args.base_url, timeout=10, headers={"Authorization": "Bearer wrong.token.value"}) as client:
        invalid_status = (await client.get("/v1/practice/cases")).status_code
    latencies = [item["latency_ms"] for item in query_results]
    return {
        "team_count": len(teams),
        "query_success": sum(item["status"] == 200 for item in query_results),
        "query_statuses": {str(code): sum(item["status"] == code for item in query_results) for code in sorted({x["status"] for x in query_results})},
        "latency_ms": {"p50": round(statistics.median(latencies), 3), "p95": round(percentile(latencies, .95), 3), "p99": round(percentile(latencies, .99), 3)},
        "submission_accepted": sum(item.get("post_status") == 202 for item in submissions),
        "submission_terminal": sum(item.get("status") in {"PREVIEW_COMPLETED", "PENDING_GT_APPROVAL"} for item in submissions),
        "identity_spoof_prevented": all(item.get("response_team_id") == item["team_id"] for item in submissions if item.get("response_team_id")),
        "cross_team_read_status": cross_team_status,
        "invalid_token_status": invalid_status,
        "submissions": submissions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the 17-team authenticated query/submission isolation test")
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--base-url", default="http://192.168.51.51:8080")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(main_async(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "submissions"}, ensure_ascii=False, indent=2))
    return 0 if result["query_success"] == 17 and result["submission_accepted"] == 17 and result["identity_spoof_prevented"] and result["cross_team_read_status"] == 404 and result["invalid_token_status"] == 401 else 1


if __name__ == "__main__":
    raise SystemExit(main())
