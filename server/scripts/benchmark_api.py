#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import httpx
import psutil


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


async def sample_resources(stop: asyncio.Event, samples: list[dict[str, float]]) -> None:
    psutil.cpu_percent(None)
    while not stop.is_set():
        memory = psutil.virtual_memory()
        samples.append({"cpu_percent": psutil.cpu_percent(None), "memory_used_bytes": float(memory.used)})
        try:
            await asyncio.wait_for(stop.wait(), 0.1)
        except TimeoutError:
            pass


async def one_client(
    endpoint: str,
    client_info: dict[str, str],
    request_count: int,
    records: list[dict[str, Any]],
) -> None:
    headers = {"Authorization": f"Bearer {client_info['token']}"}
    routes = [
        ("metrics", "/v1/cases/MOCK-SF-001/metrics", {"service": "checkout", "page_size": 100}),
        ("metrics_agg", "/v1/cases/MOCK-SF-001/metrics", {"service": "checkout", "aggregation": "avg", "step_seconds": 60}),
        ("logs", "/v1/cases/MOCK-SF-001/logs", {"service": "checkout", "keyword": "timeout", "page_size": 100}),
        ("traces", "/v1/cases/MOCK-SF-001/traces", {"status": "ERROR", "page_size": 100}),
        ("trace_detail", "/v1/cases/MOCK-SF-001/traces/00000000000000000000000000000001", {}),
        ("topology", "/v1/cases/MOCK-SF-001/topology", {}),
    ]
    async with httpx.AsyncClient(base_url=endpoint, headers=headers, timeout=10.0) as client:
        for index in range(request_count):
            name, path, params = routes[index % len(routes)]
            started = time.perf_counter()
            try:
                response = await client.get(path, params=params)
                status = response.status_code
            except httpx.HTTPError:
                status = 0
            records.append(
                {
                    "team_id": client_info["team_id"],
                    "type": name,
                    "status": status,
                    "latency_ms": (time.perf_counter() - started) * 1000,
                }
            )


async def security_checks(endpoint: str, token: str) -> dict[str, Any]:
    authorized = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=endpoint, timeout=10.0) as client:
        checks = {}
        checks["invalid_token"] = (await client.get("/v1/cases", headers={"Authorization": "Bearer invalid"})).status_code
        checks["unpublished_case"] = (
            await client.get("/v1/cases/NOT-PUBLISHED/metadata", headers=authorized)
        ).status_code
        checks["out_of_bounds"] = (
            await client.get(
                "/v1/cases/MOCK-SF-001/metrics",
                headers=authorized,
                params={"start": "2025-12-31T23:00:00Z", "end": "2026-01-01T00:00:00Z"},
            )
        ).status_code
        checks["sql_injection_literal"] = (
            await client.get(
                "/v1/cases/MOCK-SF-001/logs", headers=authorized, params={"service": "' OR 1=1 --"}
            )
        ).status_code
        checks["oversized_page"] = (
            await client.get("/v1/cases/MOCK-SF-001/logs", headers=authorized, params={"page_size": 100000})
        ).status_code
        checks["path_traversal"] = (
            await client.get("/v1/cases/..%2F..%2Fetc%2Fpasswd/metadata", headers=authorized)
        ).status_code
    return checks


async def rate_limit_check(endpoint: str, token: str, attempts: int = 65) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    statuses = []
    async with httpx.AsyncClient(base_url=endpoint, headers=headers, timeout=10.0) as client:
        for _ in range(attempts):
            statuses.append((await client.get("/v1/cases")).status_code)
    return {"attempts": attempts, "statuses": dict(Counter(statuses)), "passed": 429 in statuses}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    payload = json.loads(args.tokens.read_text(encoding="utf-8"))
    clients = payload["clients"][:17]
    if len(clients) != 17:
        raise RuntimeError("benchmark requires exactly 17 mock clients")
    records: list[dict[str, Any]] = []
    samples: list[dict[str, float]] = []
    stop = asyncio.Event()
    disk_before = psutil.disk_io_counters()
    sampler = asyncio.create_task(sample_resources(stop, samples))
    started = time.perf_counter()
    await asyncio.gather(
        *(one_client(args.endpoint, item, args.requests_per_client, records) for item in clients)
    )
    elapsed = time.perf_counter() - started
    stop.set()
    await sampler
    disk_after = psutil.disk_io_counters()
    security = await security_checks(args.endpoint, clients[0]["token"])
    rate = await rate_limit_check(args.endpoint, clients[-1]["token"])

    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_type[record["type"]].append(record)
    type_stats = {}
    for name, items in sorted(by_type.items()):
        latencies = [item["latency_ms"] for item in items]
        type_stats[name] = {
            "requests": len(items),
            "p50_ms": round(percentile(latencies, 0.50), 3),
            "p95_ms": round(percentile(latencies, 0.95), 3),
            "p99_ms": round(percentile(latencies, 0.99), 3),
            "errors": sum(item["status"] != 200 for item in items),
        }
    all_latencies = [record["latency_ms"] for record in records]
    errors = sum(record["status"] != 200 for record in records)
    result = {
        "classification": "MOCK_ONLY",
        "clients": len(clients),
        "requests": len(records),
        "elapsed_seconds": round(elapsed, 3),
        "qps": round(len(records) / elapsed, 3),
        "latency_ms": {
            "p50": round(percentile(all_latencies, 0.50), 3),
            "p95": round(percentile(all_latencies, 0.95), 3),
            "p99": round(percentile(all_latencies, 0.99), 3),
        },
        "http_errors": errors,
        "http_error_rate": round(errors / len(records), 6),
        "resources": {
            "cpu_peak_percent": round(max((sample["cpu_percent"] for sample in samples), default=0.0), 2),
            "cpu_mean_percent": round(statistics.mean([sample["cpu_percent"] for sample in samples]), 2) if samples else 0.0,
            "memory_peak_bytes": int(max((sample["memory_used_bytes"] for sample in samples), default=0)),
            "disk_read_bytes": disk_after.read_bytes - disk_before.read_bytes,
            "disk_write_bytes": disk_after.write_bytes - disk_before.write_bytes,
        },
        "by_type": type_stats,
        "security_checks": security,
        "rate_limit_check": rate,
        "note": "Mock benchmark results do not represent future full-size one-hour competition data.",
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080")
    parser.add_argument("--tokens", type=Path, default=Path("/etc/aiops-contest/mock-client-tokens.json"))
    parser.add_argument("--requests-per-client", type=int, default=12)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    security_expected = {
        "invalid_token": 401,
        "unpublished_case": 404,
        "out_of_bounds": 400,
        "sql_injection_literal": 200,
        "oversized_page": 400,
    }
    if result["http_errors"] or not result["rate_limit_check"]["passed"]:
        raise SystemExit(1)
    for key, expected in security_expected.items():
        if result["security_checks"].get(key) != expected:
            raise SystemExit(1)
    if result["security_checks"]["path_traversal"] not in {400, 404}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

