#!/usr/bin/env python3
"""Read-only five-host preflight for the new final candidate batch."""

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


HOSTS = {
    "master": ("192.168.53.52", "master"),
    "worker1": ("192.168.53.53", "w1"),
    "worker2": ("192.168.53.54", "w2"),
    "worker3": ("192.168.53.55", "w3"),
    "vm1": ("192.168.53.50", "vm1"),
}
FLOOR = 5_000_000_000
MASTER = ["ssh", "-S", "/tmp/sf-timer-master.sock", "root@192.168.53.52"]


def run(argv):
    return subprocess.check_output(argv, text=True, timeout=35).strip()


def host_command(name, command):
    address, socket = HOSTS[name]
    return run(["ssh", "-S", f"/tmp/sf-timer-{socket}.sock", f"root@{address}", command])


def filesystems(name):
    mount = "/data/stage5-v3" if name == "master" else "/data"
    result = json.loads(host_command(name, "python3 -c 'import json,shutil,os; "
        "print(json.dumps({p:{\"free_bytes\":shutil.disk_usage(p).free,"
        "\"device\":os.stat(p).st_dev} for p in [\"/\",\"" + mount + "\"] "
        "if os.path.isdir(p)}))'"))
    if name != "vm1" and (mount not in result or
                              result[mount]["device"] == result["/"]["device"]):
        raise RuntimeError(f"{name} data disk is absent or on the root filesystem")
    if any(value["free_bytes"] < FLOOR for value in result.values()):
        raise RuntimeError(f"{name} has a filesystem below the five-GB floor")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    hosts = {name: filesystems(name) for name in HOSTS}
    nodes = json.loads(run(MASTER + ["kubectl get nodes -o json"]))
    pods = json.loads(run(MASTER + ["kubectl -n otel-demo get pods -o json"]))
    wanted = ("frontend", "recommendation", "frontend-proxy", "otel-collector",
              "prometheus", "opensearch", "jaeger")
    placement = []
    for pod in pods["items"]:
        name = pod["metadata"]["name"]
        if any(name.startswith(prefix + "-") or name == prefix + "-0" for prefix in wanted):
            placement.append({"name": name, "uid": pod["metadata"]["uid"],
                "node": pod["spec"].get("nodeName"),
                "phase": pod["status"].get("phase"),
                "ready": all(c.get("ready", False)
                             for c in pod["status"].get("containerStatuses", []))})
    node_ready = {node["metadata"]["name"]: any(
        condition["type"] == "Ready" and condition["status"] == "True"
        for condition in node["status"]["conditions"]) for node in nodes["items"]}
    running = run(MASTER + ["systemctl list-units --type=service --state=running --no-legend --no-pager"])
    active = [line.split()[0] for line in running.splitlines()
              if line.split() and line.split()[0].startswith(("ops-sf", "ops-b8"))]
    config = run(MASTER + ["kubectl -n otel-demo get configmap otel-collector "
                           "-o jsonpath='{.data.relay}' | sha256sum"]).split()[0]
    prometheus = run(MASTER + ["curl -sfm 6 http://10.97.14.138:9090/-/healthy"])
    opensearch = json.loads(run(MASTER + ["curl -sfm 6 "
        "http://10.111.114.255:9200/_cluster/health"]))
    result = {"status": "READ_ONLY_PREFLIGHT_PASS", "at_utc":
              datetime.now(timezone.utc).isoformat(), "hosts": hosts,
              "node_ready": node_ready, "placement": placement,
              "active_competition_services": active, "collector_config_sha256": config,
              "prometheus": prometheus, "opensearch": {
                  "status": opensearch["status"],
                  "unassigned_shards": opensearch["unassigned_shards"]},
              "real_faults_injected_by_this_phase": 0,
              "production_attempts_created_by_this_phase": 0}
    if (len(node_ready) != 4 or not all(node_ready.values()) or active or
            config != "ad42e84b2a1d25b6b9b716f2a119f8d3ab6c50a975b860f3a8eed939618dd05f" or
            opensearch["status"] != "green" or
            any(not p["ready"] for p in placement)):
        result["status"] = "PREFLIGHT_BLOCKED"
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RuntimeError(f"Immutable snapshot already exists: {output}")
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "path": str(output),
                      "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
