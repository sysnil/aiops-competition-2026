#!/usr/bin/env python3
import os
from client import CompetitionClient

with CompetitionClient(os.getenv("AIOPS_ENDPOINT", "http://127.0.0.1:8080"), os.environ["AIOPS_TOKEN"]) as client:
    print(client.get_metrics("MOCK-SF-001", service="checkout", aggregation="avg", page_size=20))

