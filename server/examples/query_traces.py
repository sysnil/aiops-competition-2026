#!/usr/bin/env python3
import os
from client import CompetitionClient

with CompetitionClient(os.getenv("AIOPS_ENDPOINT", "http://127.0.0.1:8080"), os.environ["AIOPS_TOKEN"]) as client:
    result = client.get_traces("MOCK-SF-001", status="ERROR", page_size=10)
    print(result)
    if result["items"]:
        print(client.get_trace("MOCK-SF-001", result["items"][0]["trace_id"]))

