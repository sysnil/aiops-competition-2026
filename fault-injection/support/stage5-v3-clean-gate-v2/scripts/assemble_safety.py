"""Combine immutable snapshots and independent host evidence for safety gate."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=Path, required=True)
    parser.add_argument("--end", type=Path, required=True)
    parser.add_argument("--residue", type=Path, required=True)
    parser.add_argument("--watchdog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    start, end = read(args.start), read(args.end)
    residue, watchdog = read(args.residue), read(args.watchdog)
    age = (datetime.fromisoformat(end["captured_at_utc"]) -
           datetime.fromisoformat(start["captured_at_utc"])).total_seconds()
    start_pods, end_pods = start["running_pod_identities"], end[
        "running_pod_identities"]
    relevant = {key for key in start_pods if key.startswith("otel-demo/")}
    changed = sorted(key for key in relevant if key not in end_pods or
                     end_pods[key] != start_pods[key])
    end["components"]["no_mass_pod_restart"] = (
        "PASS" if age >= 600 and not changed else "FAIL")
    end["components"]["five_host_residue"] = (
        "PASS" if residue.get("status") == "HOST_RESIDUE_PASS" and
        len(residue.get("hosts", {})) == 5 else "FAIL")
    end["components"]["no_active_fault"] = end["components"]["five_host_residue"]
    end["components"]["independent_watchdog"] = (
        "PASS" if watchdog.get("status") == "WATCHDOG_CAPABILITY_PASS" and
        len(watchdog.get("hosts", {})) == 5 else "FAIL")
    end["health_observation_seconds"] = age
    end["changed_otel_demo_pods"] = changed
    end["derived_from_sha256"] = {str(path): sha(path) for path in
        (args.start, args.end, args.residue, args.watchdog)}
    with args.output.open("x") as stream:
        json.dump(end, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"health_observation_seconds": age,
                      "changed_otel_demo_pods": len(changed),
                      "components": end["components"]}))


if __name__ == "__main__":
    main()
