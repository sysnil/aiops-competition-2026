#!/usr/bin/env python3
"""Read-only current route and egress-qdisc scope proof for W2-to-W3 candidate."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


HOSTS = {"k8s-worker1": ("192.168.53.53", "/tmp/s5v3-worker1-20261012.sock"),
         "k8s-worker2": ("192.168.53.54", "/tmp/s5v3-worker2-20261012.sock")}


def remote(host, *command):
    address, socket = HOSTS[host]
    output = subprocess.check_output(["ssh", "-o", "BatchMode=yes", "-o",
        "ControlPath=" + socket, "root@" + address, *command],
        text=True, timeout=20)
    return json.loads(output)


def one(topology, service, node):
    rows = [item for item in topology["service_pods"].get(service, []) if item[
        "node"] == node and item["ready"]]
    if len(rows) != 1:
        raise ValueError("Nonunique required Service Pod: " + service + "/" + node)
    return rows[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topology", type=Path, required=True)
    parser.add_argument("--recommendation-ip", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    topology = json.loads(args.topology.read_text())
    checkout = one(topology, "checkout", "k8s-worker2")
    cart = one(topology, "cart", "k8s-worker3")
    image = one(topology, "image-provider", "k8s-worker2")
    frontend = one(topology, "frontend", "k8s-worker1")
    recommendation = [item for item in topology["service_pods"]["recommendation"]
                      if item["ip"] == args.recommendation_ip and item["ready"]]
    if len(recommendation) != 1:
        raise ValueError("Recommendation packet target is not a current Ready Pod")
    paths = {
        "w2_to_cart": remote("k8s-worker2", "ip", "-j", "route", "get", cart["ip"]),
        "w2_to_image": remote("k8s-worker2", "ip", "-j", "route", "get", image["ip"]),
        "w1_to_recommendation": remote("k8s-worker1", "ip", "-j", "route", "get",
                                       recommendation[0]["ip"]),
        "w2_ens160_qdisc": remote("k8s-worker2", "tc", "-j", "qdisc", "show", "dev", "ens160"),
    }
    one_route = all(len(paths[key]) == 1 for key in paths if key != "w2_ens160_qdisc")
    target_ok = one_route and paths["w2_to_cart"][0].get("dev") == "ens160" and \
        paths["w2_to_cart"][0].get("gateway") == "192.168.53.55"
    image_ok = one_route and paths["w2_to_image"][0].get("dev") != "ens160" and \
        not paths["w2_to_image"][0].get("gateway")
    recommendation_ok = one_route and paths["w1_to_recommendation"][0].get(
        "dev") == "ens160" and paths["w1_to_recommendation"][0].get(
            "gateway") == "192.168.53.55"
    qdisc_clean = len([row for row in paths["w2_ens160_qdisc"] if row.get("root")]) == 1 and \
        not any(row.get("kind") in ("netem", "tbf", "prio", "htb") for row in
                paths["w2_ens160_qdisc"])
    value = {"status": "CURRENT_ROUTE_SCOPE_METADATA_PASS" if all((target_ok, image_ok,
        recommendation_ok, qdisc_clean)) else "CURRENT_ROUTE_SCOPE_METADATA_FAIL",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "topology_sha256": hashlib.sha256(args.topology.read_bytes()).hexdigest(),
        "checkout": checkout, "cart": cart, "image_provider": image,
        "frontend_worker1": frontend, "recommendation": recommendation[0],
        "raw_routes_and_qdisc": paths,
        "w2_to_cart_via_worker3": target_ok, "w2_image_local": image_ok,
        "w1_to_recommendation_via_worker3": recommendation_ok,
        "w2_ens160_default_qdisc": qdisc_clean,
        "business_packet_proof_still_required": True, "fault_injected": False,
        "private_gt_read": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"], "target": target_ok,
                      "image_local": image_ok, "recommendation_route": recommendation_ok}))


if __name__ == "__main__":
    main()
