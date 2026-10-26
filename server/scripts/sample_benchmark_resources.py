#!/usr/bin/env python3
"""Sample API/DuckDB host resources during a remote benchmark."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import psutil


def proc_io(pid: int) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in Path(f"/proc/{pid}/io").read_text(encoding="utf-8").splitlines():
            key, value = line.split(":", 1)
            values[key] = int(value.strip())
    except (OSError, ValueError):
        pass
    return values


def active(name: str) -> str:
    return subprocess.run(
        ["systemctl", "is-active", name], check=False, capture_output=True, text=True
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, default=120.0)
    args = parser.parse_args()
    process = psutil.Process(args.pid)
    disk_before = psutil.disk_io_counters()
    io_before = proc_io(args.pid)
    process.cpu_percent(None)
    psutil.cpu_percent(None)
    samples = []
    began = time.monotonic()
    while not args.stop_file.exists() and time.monotonic() - began < args.max_seconds:
        try:
            memory = process.memory_info()
            samples.append({
                "elapsed_seconds": round(time.monotonic() - began, 3),
                "api_cpu_percent": process.cpu_percent(None),
                "api_rss_bytes": memory.rss,
                "api_vms_bytes": memory.vms,
                "api_threads": process.num_threads(),
                "system_cpu_percent": psutil.cpu_percent(None),
                "system_available_memory_bytes": psutil.virtual_memory().available,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
        time.sleep(0.1)
    disk_after = psutil.disk_io_counters()
    io_after = proc_io(args.pid)
    result = {
        "pid": args.pid,
        "duration_seconds": round(time.monotonic() - began, 3),
        "samples": len(samples),
        "api_cpu_percent": {
            "mean": round(sum(item["api_cpu_percent"] for item in samples) / len(samples), 3) if samples else 0.0,
            "peak": round(max((item["api_cpu_percent"] for item in samples), default=0.0), 3),
        },
        "system_cpu_percent": {
            "mean": round(sum(item["system_cpu_percent"] for item in samples) / len(samples), 3) if samples else 0.0,
            "peak": round(max((item["system_cpu_percent"] for item in samples), default=0.0), 3),
        },
        "api_memory": {
            "rss_peak_bytes": max((item["api_rss_bytes"] for item in samples), default=0),
            "vms_peak_bytes": max((item["api_vms_bytes"] for item in samples), default=0),
        },
        "api_threads_peak": max((item["api_threads"] for item in samples), default=0),
        "system_available_memory_min_bytes": min((item["system_available_memory_bytes"] for item in samples), default=0),
        "api_io_delta": {
            key: io_after.get(key, 0) - io_before.get(key, 0)
            for key in sorted(set(io_before) | set(io_after))
        },
        "system_disk_delta": {
            "read_bytes": disk_after.read_bytes - disk_before.read_bytes,
            "write_bytes": disk_after.write_bytes - disk_before.write_bytes,
        },
        "service_state_after": {
            "aiops-contest-api.service": active("aiops-contest-api.service"),
            "nginx.service": active("nginx.service"),
        },
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
