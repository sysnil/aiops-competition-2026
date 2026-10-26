#!/usr/bin/env python3
"""End-to-end checks for the published anonymous SF-001 API."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import duckdb
import httpx


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://192.168.51.51:8080")
    parser.add_argument("--public-root", type=Path, default=Path("/data/aiops-contest/public"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    manifest = json.loads((args.public_root / "manifests/SF-001.json").read_text(encoding="utf-8"))
    case_dir = args.public_root / "cases/SF-001" / manifest["version"]
    connection = duckdb.connect(":memory:")
    connection.execute("SET threads=2")
    checks: list[dict] = []

    def check(name: str, passed: bool, detail=None) -> None:
        checks.append({"name": name, "pass": bool(passed), "detail": detail})

    with httpx.Client(base_url=base, timeout=10.0, headers={"X-Team-ID": "server-local-e2e"}) as client:
        response = client.get("/healthz")
        check("healthz", response.status_code == 200 and response.json().get("status") == "ok", response.status_code)

        response = client.get("/v1/cases")
        cases = response.json().get("items", []) if response.status_code == 200 else []
        check("anonymous_case_list", response.status_code == 200, response.status_code)
        check("public_case_visible", [item.get("case_id") for item in cases] == ["SF-001"], cases)
        check("mock_case_hidden", all(item.get("classification") != "MOCK_ONLY" for item in cases), cases)

        response = client.get("/v1/cases/SF-001/metadata")
        metadata = response.json() if response.status_code == 200 else {}
        check("metadata", response.status_code == 200, response.status_code)
        check(
            "metadata_window",
            metadata.get("start_time") == manifest["start_time"] and metadata.get("end_time") == manifest["end_time"],
            {"api": [metadata.get("start_time"), metadata.get("end_time")]},
        )

        response = client.get("/v1/cases/SF-001/topology")
        topology = response.json().get("topology") if response.status_code == 200 else None
        check("topology", response.status_code == 200 and topology == manifest["public_topology"], response.status_code)

        metric_path = str(case_dir / "metrics.parquet")
        metric_row = connection.execute(
            "SELECT CAST(timestamp AS VARCHAR), service, pod FROM read_parquet(?) "
            "WHERE service IS NOT NULL AND service<>'' AND pod IS NOT NULL AND pod<>'' "
            "ORDER BY timestamp, service, pod LIMIT 1", [metric_path]
        ).fetchone()
        metric_start = datetime.fromisoformat(metric_row[0].replace("+00", "+00:00"))
        response = client.get("/v1/cases/SF-001/metrics", params={
            "start": iso(metric_start), "end": iso(metric_start + timedelta(seconds=1)),
            "service": metric_row[1], "pod": metric_row[2], "page_size": 100,
        })
        metric_items = response.json().get("items", []) if response.status_code == 200 else []
        check("metrics_filter", response.status_code == 200 and metric_items and all(
            item.get("service") == metric_row[1] and item.get("pod") == metric_row[2] for item in metric_items
        ), {"status": response.status_code, "items": len(metric_items), "service": metric_row[1], "pod": metric_row[2]})

        log_path = str(case_dir / "logs.parquet")
        log_row = connection.execute(
            "SELECT CAST(timestamp AS VARCHAR), service, body FROM read_parquet(?) "
            "WHERE service IS NOT NULL AND service<>'' AND lower(body) LIKE '%error%' "
            "ORDER BY timestamp LIMIT 1", [log_path]
        ).fetchone()
        log_start = datetime.fromisoformat(log_row[0].replace("+00", "+00:00"))
        response = client.get("/v1/cases/SF-001/logs", params={
            "start": iso(log_start - timedelta(milliseconds=1)),
            "end": iso(log_start + timedelta(seconds=1)),
            "service": log_row[1], "keyword": "error", "page_size": 100,
        })
        log_items = response.json().get("items", []) if response.status_code == 200 else []
        check("logs_filter_keyword", response.status_code == 200 and log_items and all(
            item.get("service") == log_row[1] and "error" in item.get("body", "").lower() for item in log_items
        ), {"status": response.status_code, "items": len(log_items), "service": log_row[1]})

        trace_path = str(case_dir / "traces.parquet")
        trace_row = connection.execute(
            "SELECT CAST(start_time AS VARCHAR), service FROM read_parquet(?) "
            "WHERE service IS NOT NULL AND service<>'' ORDER BY start_time LIMIT 1", [trace_path]
        ).fetchone()
        trace_start = datetime.fromisoformat(trace_row[0].replace("+00", "+00:00"))
        response = client.get("/v1/cases/SF-001/traces", params={
            "start": iso(trace_start), "end": iso(trace_start + timedelta(seconds=1)),
            "service": trace_row[1], "page_size": 100,
        })
        trace_items = response.json().get("items", []) if response.status_code == 200 else []
        check("traces_filter", response.status_code == 200 and trace_items and all(
            item.get("service") == trace_row[1] for item in trace_items
        ), {"status": response.status_code, "items": len(trace_items), "service": trace_row[1]})

        sample_trace_id = manifest["sample_trace_id"]
        response = client.get(f"/v1/cases/SF-001/traces/{sample_trace_id}")
        spans = response.json().get("spans", []) if response.status_code == 200 else []
        services = sorted({span.get("service") for span in spans})
        check("trace_detail", response.status_code == 200 and spans, {"status": response.status_code, "spans": len(spans)})
        check("cross_service_frontend_checkout", {"frontend", "checkout"}.issubset(services), services)

        params = {"page_size": 20}
        page1a = client.get("/v1/cases/SF-001/logs", params={**params, "page": 1})
        page1b = client.get("/v1/cases/SF-001/logs", params={**params, "page": 1})
        page2 = client.get("/v1/cases/SF-001/logs", params={**params, "page": 2})
        items1 = page1a.json().get("items", []) if page1a.status_code == 200 else []
        items2 = page2.json().get("items", []) if page2.status_code == 200 else []
        check("stable_pagination", page1a.status_code == page1b.status_code == page2.status_code == 200 and page1a.json() == page1b.json(), None)
        check("pagination_advances", items1 != items2, None)

        null_log = connection.execute(
            "SELECT CAST(timestamp AS VARCHAR), service FROM read_parquet(?) WHERE severity IS NULL ORDER BY timestamp LIMIT 1",
            [log_path],
        ).fetchone()
        null_start = datetime.fromisoformat(null_log[0].replace("+00", "+00:00"))
        response = client.get("/v1/cases/SF-001/logs", params={
            "start": iso(null_start), "end": iso(null_start + timedelta(milliseconds=1)),
            "service": null_log[1], "page_size": 1000,
        })
        null_items = response.json().get("items", []) if response.status_code == 200 else []
        check("null_log_severity", response.status_code == 200 and any(item.get("severity") is None for item in null_items), len(null_items))

        case_end = datetime.fromisoformat(manifest["end_time"].replace("Z", "+00:00"))
        response = client.get("/v1/cases/SF-001/metrics", params={
            "start": iso(case_end + timedelta(hours=1)),
            "end": iso(case_end + timedelta(hours=1, minutes=1)),
        })
        check("outside_window_empty", response.status_code == 200 and response.json().get("items") == [], response.status_code)

        response = client.get("/v1/cases/NOT-PUBLISHED/metadata")
        check("unpublished_hidden", response.status_code == 404, response.status_code)
        response = client.get("/v1/cases/SF-001/metrics", params={"service": "x' OR 1=1 --", "page_size": 10})
        check("sql_injection_literal", response.status_code == 200 and response.json().get("items") == [], response.status_code)
        response = client.get("/v1/cases/SF-001/metrics", params={"page_size": 1001})
        check("page_limit", response.status_code in {400, 422}, response.status_code)
        response = client.get("/v1/cases/" + quote("../../etc/passwd", safe="") + "/metadata")
        check("path_traversal_blocked", response.status_code in {400, 404}, response.status_code)
        response = client.get("/data/aiops-contest/staging/SF-001-incoming/manifest.json")
        check("staging_not_exposed", response.status_code == 404, response.status_code)
        response = client.post("/v1/cases")
        check("write_method_blocked", response.status_code in {403, 405}, response.status_code)

    expected_counts = {"metrics": 80358, "logs": 567346, "traces": 817529, "trace_index": 119091}
    direct_counts = {
        "metrics": connection.execute("SELECT count(*) FROM read_parquet(?)", [metric_path]).fetchone()[0],
        "logs": connection.execute("SELECT count(*) FROM read_parquet(?)", [log_path]).fetchone()[0],
        "traces": connection.execute("SELECT count(*) FROM read_parquet(?)", [trace_path]).fetchone()[0],
        "trace_index": connection.execute("SELECT count(*) FROM read_parquet(?)", [str(case_dir / "trace_index.parquet")]).fetchone()[0],
    }
    check("published_parquet_counts", direct_counts == expected_counts, direct_counts)
    failures = [item for item in checks if not item["pass"]]
    result = {
        "status": "PASS" if not failures else "FAIL",
        "base_url": base,
        "case_id": "SF-001",
        "checks_passed": len(checks) - len(failures),
        "checks_total": len(checks),
        "failures": failures,
        "checks": checks,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    raise SystemExit(0 if not failures else 1)


if __name__ == "__main__":
    main()
