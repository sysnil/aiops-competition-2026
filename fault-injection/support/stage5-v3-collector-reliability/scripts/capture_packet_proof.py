#!/usr/bin/env python3
"""Bounded, read-only observation of ordinary Checkout-to-Cart packets."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


HOSTS = {
    "k8s-worker1": ("192.168.53.53", "/tmp/s5v3-worker1-20261012.sock"),
    "k8s-worker2": ("192.168.53.54", "/tmp/s5v3-worker2-20261012.sock"),
    "k8s-worker3": ("192.168.53.55", "/tmp/s5v3-worker3-20261012.sock"),
}
IP = re.compile(r"^10\.244\.\d{1,3}\.\d{1,3}$")


def remote(host, command, timeout):
    address, socket = HOSTS[host]
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ControlPath=" + socket,
                           "root@" + address, *command], capture_output=True, text=True, timeout=timeout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-ip", required=True)
    parser.add_argument("--destination-ip", required=True)
    parser.add_argument("--source-host", choices=sorted(HOSTS), required=True)
    parser.add_argument("--destination-host", choices=sorted(HOSTS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (args.output.exists() or not IP.fullmatch(args.source_ip) or
            not IP.fullmatch(args.destination_ip) or args.source_host == args.destination_host):
        raise ValueError("New output and two bounded Pod IPs required")
    evidence = {}
    for host in (args.source_host, args.destination_host):
        route_target = args.destination_ip if host == args.source_host else args.source_ip
        route = remote(host, ["ip", "route", "get", route_target], 12)
        if route.returncode:
            raise RuntimeError("Route lookup failed on " + host)
        expression = f"host {args.source_ip} and host {args.destination_ip}"
        capture = remote(host, ["timeout", "25", "tcpdump", "-i", "any", "-nn", "-tttt",
                                "-c", "100", expression], 35)
        if capture.returncode not in (0, 124):
            raise RuntimeError("Packet capture failed on " + host + ": " + capture.stderr[-300:])
        packets = [line for line in capture.stdout.splitlines() if args.source_ip in line
                   and args.destination_ip in line]
        evidence[host] = {"route": route.stdout.strip(), "capture_exit_code": capture.returncode,
                          "packet_count": len(packets), "packet_lines": packets,
                          "stderr_sha256": hashlib.sha256(capture.stderr.encode()).hexdigest()}
    result = {"status": "BUSINESS_PACKET_PROOF_PRESENT" if all(row["packet_count"] > 0
        for row in evidence.values()) else "BUSINESS_PACKET_PROOF_ABSENT",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_pod_ip": args.source_ip, "destination_pod_ip": args.destination_ip,
        "source_host": args.source_host, "destination_host": args.destination_host,
        "hosts": evidence, "traffic_generated_by_script": False,
        "fault_injected": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "packet_counts": {
        host: row["packet_count"] for host, row in evidence.items()}}))


if __name__ == "__main__":
    main()
