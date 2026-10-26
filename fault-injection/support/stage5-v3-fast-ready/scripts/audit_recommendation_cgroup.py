#!/usr/bin/env python3
"""Read-only UID/container/cgroup qualification for both Recommendation Pods."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess


MIB = 1024 * 1024


def task_pid(container_id):
    rows = [line.split() for line in subprocess.check_output(["ctr", "-n", "k8s.io",
        "tasks", "list"], text=True, timeout=20).splitlines()]
    matches = [int(row[1]) for row in rows if len(row) >= 3 and
               row[0] == container_id and row[2] == "RUNNING"]
    if len(matches) != 1:
        raise ValueError("Container task is not uniquely RUNNING")
    return matches[0]


def one(pod):
    container = pod["container_ids"][0].removeprefix("containerd://")
    pid = task_pid(container)
    relation = Path("/proc") / str(pid) / "cgroup"
    text = relation.read_text().strip()
    if "::" not in text:
        raise ValueError("Expected cgroup v2")
    relative = text.split("::", 1)[1].lstrip("/")
    if pod["uid"].replace("-", "_") not in relative or container not in relative:
        raise ValueError("Pod UID/container cgroup relation mismatched")
    path = Path("/sys/fs/cgroup") / relative
    if not path.is_dir():
        raise ValueError("Bound cgroup path missing")
    memory_max = int((path / "memory.max").read_text().strip())
    memory_now = int((path / "memory.current").read_text().strip())
    bounded = min(int((memory_max - memory_now) * 0.55),
                  int(memory_max * 0.80) - memory_now)
    cpu_stat = {key: int(value) for line in (path / "cpu.stat").read_text().splitlines()
                for key, value in [line.split()]}
    return {"pod_name": pod["name"], "pod_uid": pod["uid"],
        "container_id": container, "pid": pid, "cgroup_path": str(path),
        "memory_current_bytes": memory_now, "memory_max_bytes": memory_max,
        "bounded_memory_headroom_bytes": bounded,
        "bounded_memory_precheck": bounded >= 32 * MIB and
            memory_now + bounded <= memory_max * 0.80,
        "cpu_max": (path / "cpu.max").read_text().strip(),
        "cpu_stat": cpu_stat}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topology", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or socket.gethostname() != "k8s-worker3":
        raise ValueError("New output on Worker3 required")
    topology = json.loads(args.topology.read_text())
    pods = [item for item in topology["service_pods"].get("recommendation", [])
            if item["ready"] and item["node"] == "k8s-worker3"]
    if len(pods) != 2:
        raise ValueError("Recommendation does not have two Ready Worker3 Pods")
    rows = [one(pod) for pod in pods]
    value = {"status": "RECOMMENDATION_CGROUP_PREFLIGHT_PASS"
        if all(row["bounded_memory_precheck"] for row in rows) else
        "RECOMMENDATION_CGROUP_PREFLIGHT_BLOCKED",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(), "pods": rows,
        "all_same_uid_container_identity": True,
        "read_only": True, "fault_injected": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
        "headroom_bytes": [row["bounded_memory_headroom_bytes"] for row in rows]}))


if __name__ == "__main__":
    main()
