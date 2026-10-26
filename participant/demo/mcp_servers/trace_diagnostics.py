from __future__ import annotations

import os
import statistics
import time
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

from detectors.common import percentile


SERVER = MCPServer(
    name="trace-diagnostics",
    instructions="Read-only trace diagnostics against the contest Public API. Never accesses files, SQL, staging, or Private GT.",
)


def _get_with_retry(client: httpx.Client, path: str, **kwargs: Any) -> httpx.Response:
    """Bound retries for transient Public API throttling/server failures."""
    response: httpx.Response | None = None
    for attempt in range(3):
        response = client.get(path, **kwargs)
        if response.status_code not in {429, 502, 503, 504} or attempt == 2:
            return response
        try:
            retry_after = float(response.headers.get("Retry-After", "0"))
        except ValueError:
            retry_after = 0
        time.sleep(min(max(retry_after, 0.5 * (2**attempt)), 2.0))
    assert response is not None
    return response


def _edge_samples(
    client: httpx.Client,
    case_id: str,
    source_service: str,
    target_service: str,
    start: str,
    end: str,
    max_traces: int,
    case_prefix: str,
) -> tuple[list[float], list[dict[str, Any]], list[str], int]:
    response = _get_with_retry(
        client,
        f"{case_prefix}/traces",
        params={
            "start": start,
            "end": end,
            "service": target_service,
            "parent_service": source_service,
            "page": 1,
            "page_size": min(max(max_traces * 5, 20), 200),
        },
    )
    response.raise_for_status()
    listing = response.json().get("items", [])
    trace_ids: list[str] = []
    for span in listing:
        trace_id = span.get("trace_id")
        if trace_id and trace_id not in trace_ids:
            trace_ids.append(trace_id)
        if len(trace_ids) >= max_traces:
            break
    durations: list[float] = []
    exemplars: list[dict[str, Any]] = []
    warnings: list[str] = []
    request_count = 1
    for trace_id in trace_ids:
        detail_response = _get_with_retry(client, f"{case_prefix}/traces/{trace_id}")
        request_count += 1
        if detail_response.status_code != 200:
            warnings.append(f"trace detail unavailable: {trace_id}")
            continue
        spans = detail_response.json().get("spans", detail_response.json().get("items", []))
        by_id = {span.get("span_id"): span for span in spans if span.get("span_id")}
        for child in spans:
            if child.get("service") != target_service:
                continue
            parent = by_id.get(child.get("parent_span_id"))
            if not parent or parent.get("service") != source_service:
                continue
            try:
                duration = float(child["duration_ms"])
            except (KeyError, TypeError, ValueError):
                continue
            durations.append(duration)
            if len(exemplars) < 5:
                exemplars.append(
                    {
                        "trace_id": trace_id,
                        "parent_span_id": parent.get("span_id"),
                        "child_span_id": child.get("span_id"),
                        "source_service": parent.get("service"),
                        "target_service": child.get("service"),
                        "duration_ms": duration,
                        "start_time": child.get("start_time"),
                    }
                )
    if not trace_ids:
        warnings.append("no matching traces returned for this edge/window")
    if listing and len(trace_ids) >= max_traces:
        warnings.append(f"bounded sample: at most {max_traces} traces were validated")
    return durations, exemplars, warnings, request_count


def _stats(values: list[float]) -> dict[str, Any]:
    return {
        "sample_count": len(values),
        "median_ms": round(statistics.median(values), 6) if values else None,
        "p95_ms": round(percentile(values, 0.95), 6) if values else None,
    }


def analyze_edge_latency_impl(
    base_url: str,
    case_id: str,
    source_service: str,
    target_service: str,
    baseline_start: str,
    baseline_end: str,
    analysis_start: str,
    analysis_end: str,
    max_traces: int = 8,
    mode: str = "practice",
    team_token: str | None = None,
    final_task_id: str | None = None,
) -> dict[str, Any]:
    max_traces = min(max(int(max_traces), 1), 20)
    headers = {"User-Agent": "aiops-trace-mcp/2.0"}
    if team_token:
        headers["Authorization"] = f"Bearer {team_token}"
    if mode == "final":
        if not final_task_id:
            raise ValueError("AIOPS_FINAL_TASK_ID is required in final mode")
        api_root = f"{base_url.rstrip('/')}/v1/final/tasks/{final_task_id}/case"
        case_prefix = ""
    else:
        api_root = f"{base_url.rstrip('/')}/v1/{mode}"
        case_prefix = f"/cases/{case_id}"
    with httpx.Client(
        base_url=api_root,
        timeout=8.0,
        headers=headers,
    ) as client:
        base_values, base_evidence, base_warnings, base_requests = _edge_samples(
            client, case_id, source_service, target_service,
            baseline_start, baseline_end, max_traces, case_prefix,
        )
        analysis_values, analysis_evidence, analysis_warnings, analysis_requests = _edge_samples(
            client, case_id, source_service, target_service,
            analysis_start, analysis_end, max_traces, case_prefix,
        )
    baseline = _stats(base_values)
    analysis = _stats(analysis_values)
    warnings = base_warnings + analysis_warnings
    conclusion = "INSUFFICIENT_EVIDENCE"
    ratio = None
    if baseline["sample_count"] >= 2 and analysis["sample_count"] >= 2:
        if baseline["p95_ms"] and analysis["p95_ms"] is not None:
            ratio = analysis["p95_ms"] / max(baseline["p95_ms"], 1e-9)
            conclusion = "SUPPORTED" if ratio >= 1.5 else "NOT_SUPPORTED"
    return {
        "case_id": case_id,
        "source_service": source_service,
        "target_service": target_service,
        "baseline_window": {"start": baseline_start, "end": baseline_end},
        "analysis_window": {"start": analysis_start, "end": analysis_end},
        "baseline": baseline,
        "analysis": analysis,
        "p95_ratio": round(ratio, 6) if ratio is not None else None,
        "baseline_count": baseline["sample_count"],
        "analysis_count": analysis["sample_count"],
        "baseline_p95": baseline["p95_ms"],
        "analysis_p95": analysis["p95_ms"],
        "latency_ratio": round(ratio, 6) if ratio is not None else None,
        "exemplar_trace_ids": sorted({x["trace_id"] for x in base_evidence + analysis_evidence}),
        "evidence_references": base_evidence + analysis_evidence,
        "coverage_warnings": warnings,
        "supported_conclusion": conclusion,
        "evidence": {"baseline": base_evidence, "analysis": analysis_evidence},
        "warnings": warnings,
        "api_request_count": base_requests + analysis_requests,
    }


@SERVER.tool(
    name="analyze_edge_latency",
    description="Compare an observed parent/child service edge across baseline and analysis windows using trace details from the Public API.",
    structured_output=True,
)
def analyze_edge_latency(
    case_id: str,
    source_service: str,
    target_service: str,
    baseline_start: str,
    baseline_end: str,
    analysis_start: str,
    analysis_end: str,
    max_traces: int = 8,
) -> dict[str, Any]:
    return analyze_edge_latency_impl(
        os.environ.get("AIOPS_BASE_URL", "http://192.168.51.51:8080"),
        case_id, source_service, target_service,
        baseline_start, baseline_end, analysis_start, analysis_end, max_traces,
        os.environ.get("AIOPS_MODE", "practice"),
        os.environ.get("AIOPS_TEAM_TOKEN") or None,
        os.environ.get("AIOPS_FINAL_TASK_ID") or None,
    )


if __name__ == "__main__":
    SERVER.run(transport="stdio")
