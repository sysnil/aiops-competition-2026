#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from client import CompetitionClient


with CompetitionClient(os.getenv("AIOPS_ENDPOINT", "http://127.0.0.1:8080"), os.environ["AIOPS_TOKEN"]) as client:
    metrics = client.get_metrics("MOCK-SF-001", service="checkout", aggregation="max", page_size=100)
    logs = client.get_logs("MOCK-SF-001", service="checkout", keyword="timeout", page_size=100)
    traces = client.get_traces("MOCK-SF-001", service="checkout", status="ERROR", page_size=100)
    finding = {
        "classification": "MOCK_ONLY",
        "finding": "checkout 在模拟窗口内出现高延迟并伴随数据库超时日志和错误 Span",
        "evidence_counts": {
            "metrics": len(metrics["items"]), "logs": len(logs["items"]), "traces": len(traces["items"])
        },
    }
    print(json.dumps(finding, ensure_ascii=False, indent=2))

