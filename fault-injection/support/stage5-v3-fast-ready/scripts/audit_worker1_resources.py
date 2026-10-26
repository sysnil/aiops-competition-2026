#!/usr/bin/env python3
"""Read-only Worker1 resource activity and candidate Node controls."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket


BUSINESS = {"frontend", "ad", "payment", "shipping", "fraud-detection",
            "cart", "checkout", "recommendation", "product-catalog", "email"}


def pressure(path):
    values = {}
    for line in path.read_text().splitlines():
        name, *tokens = line.split()
        values[name] = {key: float(value) if key.startswith("avg") else int(value)
                        for token in tokens for key, value in [token.split("=", 1)]}
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topology", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or socket.gethostname() != "k8s-worker1":
        raise ValueError("New output on Worker1 required")
    topology = json.loads(args.topology.read_text())
    pods = [(service, item["name"], item["uid"]) for service, rows in
            topology["service_pods"].items() for item in rows
            if service in BUSINESS and item["node"] == "k8s-worker1" and item["ready"]]
    meminfo = {line.split(":", 1)[0]: int(line.split()[1]) * 1024 for line in
               Path("/proc/meminfo").read_text().splitlines() if line.startswith(
                   ("MemTotal:", "MemAvailable:"))}
    disk = shutil.disk_usage("/")
    cpu = pressure(Path("/proc/pressure/cpu"))
    value = {"status": "WORKER1_RESOURCE_BASELINE_OBSERVED",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(), "logical_cpus": os.cpu_count(),
        "cpu_psi": cpu, "memory": meminfo,
        "root_disk_free_bytes": disk.free,
        "ready_business_pods": [{"service": service, "pod": name, "uid": uid}
                                for service, name, uid in pods],
        "node_fault_during_safety_contract_approved": False,
        "read_only": True, "fault_injected": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "business_pods": len(pods),
                      "cpu_psi_some_avg60": cpu["some"]["avg60"]}))


if __name__ == "__main__":
    main()
