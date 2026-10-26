"""Bounded 30-second host PSI and four-node CPU safety evidence for clean runs."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.parse
import urllib.request


PROM = "http://10.97.14.138:9090"
HOSTS = {
    "k8s-master": ("192.168.53.52", None),
    "k8s-worker1": ("192.168.53.53", "/tmp/s5v3cgv2-worker1.sock"),
    "k8s-worker2": ("192.168.53.54", "/tmp/stage5v3-w2-archive.sock"),
    "k8s-worker3": ("192.168.53.55", "/tmp/stage5v3-w3-archive.sock"),
}
PSI = re.compile(r"\bsome\s+avg10=[0-9.]+\s+avg60=([0-9.]+)")


def allocatable(raw):
    return float(raw[:-1]) / 1000 if raw.endswith("m") else float(raw)


def classify(cpu_cores, psi_percent, capacity):
    reserve = max(2.0, .25 * capacity)
    return {"headroom_cores": capacity - cpu_cores,
            "reserve_required_cores": reserve,
            "cpu_safety_ok": cpu_cores <= capacity - reserve and
                psi_percent <= 5.0}


def utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def query_cpu():
    url = PROM + "/api/v1/query?" + urllib.parse.urlencode({
        "query": "k8s_node_cpu_usage"})
    with urllib.request.urlopen(url, timeout=10) as response:
        raw = json.load(response)
    if raw.get("status") != "success":
        raise RuntimeError("Prometheus Node CPU query failed")
    rows = {}
    now = time.time()
    for row in raw["data"]["result"]:
        name = row["metric"].get("k8s_node_name")
        if name in HOSTS:
            stamp, value = row["value"]
            if not 0 <= now - float(stamp) <= 60:
                raise RuntimeError("Node CPU sample stale")
            rows[name] = float(value)
    if set(rows) != set(HOSTS):
        raise RuntimeError("Four-node CPU sample unavailable")
    return rows


def psi_sample(node):
    if node == "k8s-master":
        text = Path("/proc/pressure/cpu").read_text()
    else:
        ip, socket = HOSTS[node]
        result = subprocess.run(["ssh", "-S", socket, "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=3", "root@" + ip, "cat",
            "/proc/pressure/cpu"], capture_output=True, text=True,
            timeout=6, check=True)
        text = result.stdout
    found = PSI.search(text)
    if not found:
        raise RuntimeError("CPU PSI some avg60 unavailable: " + node)
    return float(found.group(1))


def capacities():
    raw = json.loads(subprocess.check_output(["kubectl", "get", "nodes",
        "-o", "json"], text=True, timeout=15))
    value = {row["metadata"]["name"]: allocatable(row["status"][
        "allocatable"]["cpu"]) for row in raw["items"]}
    if set(value) != set(HOSTS):
        raise RuntimeError("Node allocatable CPU changed or missing")
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--duration", type=int, choices=(1500,), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00")).timestamp()
    args.output.mkdir(parents=True)
    capacity = capacities()
    seen = []
    bad_since = {name: None for name in HOSTS}
    outcome = "CPU_SAFETY_PASS"
    reason = None
    path = args.output / "samples.jsonl"
    try:
        with path.open("x") as stream:
            for index in range(50):
                planned = start + 15 + index * 30
                time.sleep(max(0, planned - time.time()))
                if time.time() - planned > 10:
                    raise RuntimeError("CPU safety sampler missed planned point")
                cpu = query_cpu()
                rows = {}
                sustained = []
                for name in HOSTS:
                    pressure = psi_sample(name)
                    row = {"cpu_cores": cpu[name],
                           "psi_some_avg60_percent": pressure,
                           "allocatable_cores": capacity[name],
                           **classify(cpu[name], pressure, capacity[name])}
                    rows[name] = row
                    bad_since[name] = (None if row["cpu_safety_ok"] else
                        planned if bad_since[name] is None else bad_since[name])
                    if bad_since[name] is not None and planned - bad_since[name] >= 60:
                        sustained.append(name)
                sample = {"index": index, "planned_utc": utc(planned),
                          "observed_utc": utc(time.time()), "nodes": rows}
                seen.append(sample)
                stream.write(json.dumps(sample) + "\n")
                stream.flush()
                if sustained:
                    raise RuntimeError("Sustained CPU Safety violation: " +
                                       ",".join(sustained))
    except Exception as exc:
        outcome = "CPU_SAFETY_FAIL_CLOSED"
        reason = type(exc).__name__ + ": " + str(exc)
    manifest = {"status": outcome, "reason": reason,
        "planned_samples": 50, "recorded_samples": len(seen),
        "schedule_seconds": 30, "start_utc": utc(start),
        "finish_utc": utc(time.time()),
        "capacity_cores": capacity,
        "max_cpu_cores": {name: max((row["nodes"][name]["cpu_cores"]
            for row in seen), default=None) for name in HOSTS},
        "max_psi_some_avg60_percent": {name: max((row["nodes"][name][
            "psi_some_avg60_percent"] for row in seen), default=None)
            for name in HOSTS},
        "samples_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "fault_injected": False}
    with (args.output / "manifest.json").open("x") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": outcome, "samples": len(seen),
                      "reason": reason}))
    if outcome != "CPU_SAFETY_PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
