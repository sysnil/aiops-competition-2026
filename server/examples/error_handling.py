#!/usr/bin/env python3
import os
from client import CompetitionAPIError, CompetitionClient

try:
    with CompetitionClient(os.getenv("AIOPS_ENDPOINT", "http://127.0.0.1:8080"), os.environ["AIOPS_TOKEN"]) as client:
        client.get_metrics("NOT-PUBLISHED", page_size=10)
except CompetitionAPIError as exc:
    print({"status": exc.status_code, "code": exc.code, "request_id": exc.request_id})

