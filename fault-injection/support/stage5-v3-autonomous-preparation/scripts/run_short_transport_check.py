#!/usr/bin/env python3
"""Three-minute no-fault transport check after a reversible proxy config trial."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request


ROOT = Path("/root/stage5-v3-autonomous-preparation")
GENERATOR = Path("/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py")
PEOPLE = Path("/root/cross-layer-workload-generation/design/people.json")
ADMIN = "http://10.244.126.7:10000/stats?filter=cluster.frontend.*(upstream_rq_5xx|upstream_cx_destroy_remote_with_active_rq)"


def stats():
    # Proxy Pod IP may change after rollout; resolve it from the current Ready Pod.
    items = json.loads(subprocess.check_output(["kubectl", "-n", "otel-demo",
        "get", "pods", "-l", "opentelemetry.io/name=frontend-proxy", "-o", "json"],
        text=True, timeout=30))["items"]
    ready = [row for row in items if all(x.get("ready") for x in
             row["status"].get("containerStatuses", []))]
    if len(ready) != 1:
        raise ValueError("Current proxy is not uniquely Ready")
    ip = ready[0]["status"]["podIP"]
    with urllib.request.urlopen(ADMIN.replace("10.244.126.7", ip), timeout=5) as response:
        raw = response.read().decode()
    values = {line.split(": ", 1)[0]: int(line.split(": ", 1)[1])
              for line in raw.splitlines() if ": " in line}
    return {"pod": ready[0]["metadata"]["name"],
            "pod_uid": ready[0]["metadata"]["uid"], "counters": values}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite a transport check")
    args.output.mkdir(parents=True)
    before = stats()
    command = ["python3", str(GENERATOR), "--machine",
        str(ROOT / "design/workload-state-machine-c2.yaml"), "--profiles",
        str(ROOT / "design/load-profiles-c2.yaml"), "--people", str(PEOPLE),
        "--profile", "MEDIUM", "--seed", str(args.seed), "--duration", "180",
        "--base-url", "http://10.104.177.128:8080",
        "--output", str(args.output / "state-machine"), "--allow-errors"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=220)
    (args.output / "stdout.txt").write_text(result.stdout)
    (args.output / "stderr.txt").write_text(result.stderr)
    after = stats()
    sessions = args.output / "state-machine/sessions.jsonl"
    rows = [json.loads(line) for line in sessions.read_text().splitlines()] if \
        sessions.exists() else []
    steps = [step for row in rows for step in row["steps"]]
    errors = [step for step in steps if not step.get("success")]
    deltas = {name: after["counters"].get(name, 0) - value
              for name, value in before["counters"].items()}
    status = "TRANSPORT_SMOKE_PASS" if result.returncode == 0 and rows and \
        not errors and all(value == 0 for value in deltas.values()) else \
        "TRANSPORT_SMOKE_FAILED"
    summary = {"status": status, "at_utc": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed, "duration_seconds": 180, "command": command,
        "exit_code": result.returncode, "requests": len(steps), "errors": errors,
        "before": before, "after": after, "counter_deltas": deltas,
        "generator_sha256": sha(GENERATOR),
        "machine_sha256": sha(ROOT / "design/workload-state-machine-c2.yaml"),
        "profile_sha256": sha(ROOT / "design/load-profiles-c2.yaml"),
        "sessions_sha256": sha(sessions) if sessions.exists() else None,
        "formal_case_created": False, "faults_injected": 0}
    with (args.output / "summary.json").open("x") as stream:
        json.dump(summary, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": status, "requests": len(steps),
                      "errors": len(errors), "counter_deltas": deltas}))
    if status != "TRANSPORT_SMOKE_PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
