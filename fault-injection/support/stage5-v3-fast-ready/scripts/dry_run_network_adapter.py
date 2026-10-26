#!/usr/bin/env python3
"""Structured W2->W3 tc scope audit; contains no mutation command execution."""

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import subprocess

import yaml


WORKER2 = ("192.168.53.54", "/tmp/s5v3disk-w2.sock")


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def route(*args):
    return json.loads(subprocess.check_output(["ssh", "-S", WORKER2[1],
        "-o", "BatchMode=yes", "root@" + WORKER2[0], *args],
        text=True, timeout=15))


def one(topology, service, node):
    rows = [item for item in topology["service_pods"].get(service, [])
            if item["node"] == node and item["ready"]]
    if len(rows) != 1:
        raise ValueError("Nonunique current service/Pod placement: " + service)
    return rows[0]


def endpoint(topology, service, pod):
    return any(row["pod_uid"] == pod["uid"] and pod["ip"] in row["addresses"]
               and row["ready"] is True for row in topology["endpoint_slices"].get(
                   service, []))


def decide(plan, topology, target, payment, paths, live):
    checkout = one(topology, "checkout", "k8s-worker2")
    cart = one(topology, "cart", "k8s-worker3")
    image = one(topology, "image-provider", "k8s-worker2")
    paid = one(topology, "payment", "k8s-worker1")
    prefix = ipaddress.ip_network(plan["destination_pod_cidr"], strict=True)
    current = (plan["status"] == "DRAFT_DRY_RUN_ONLY" and
        plan["injection_this_round"] == "forbidden" and
        plan["target_path"] == "NETWORK_PATH/k8s-worker2/k8s-worker3" and
        ipaddress.ip_address(cart["ip"]) in prefix and
        all(endpoint(topology, service, pod) for service, pod in (
            ("checkout", checkout), ("cart", cart),
            ("image-provider", image), ("payment", paid))) and
        target["status"] == payment["status"] == "BUSINESS_PACKET_PROOF_PRESENT" and
        (target["source_pod_ip"], target["destination_pod_ip"]) ==
        (checkout["ip"], cart["ip"]) and
        (payment["source_pod_ip"], payment["destination_pod_ip"]) ==
        (checkout["ip"], paid["ip"]) and
        all(item["packet_count"] > 0 for proof in (target, payment)
            for item in proof["hosts"].values()) and
        paths["w2_to_cart_via_worker3"] and paths["w2_image_local"] and
        paths["w2_ens160_default_qdisc"] and
        len(live["target_route"]) == len(live["payment_route"]) ==
        len(live["destination_node_route"]) == 1 and
        live["target_route"][0].get("gateway") == "192.168.53.55" and
        live["target_route"][0].get("dev") == "ens160" and
        live["payment_route"][0].get("gateway") == "192.168.53.53" and
        live["destination_node_route"][0].get("dev") == "ens160" and
        live["pod_cidr_route"] == [{"dst": str(prefix), "gateway":
            "192.168.53.55", "dev": "ens160", "protocol": "bird", "metric": 1024,
            "flags": []}])
    interface = plan["source_interface"]
    preview = [
        ["tc", "qdisc", "replace", "dev", interface, "root", "handle", "1:",
         "prio", "bands", "3"],
        ["tc", "qdisc", "add", "dev", interface, "parent", "1:3",
         "handle", "30:", "netem", "loss", "10%"],
        ["tc", "filter", "add", "dev", interface, "protocol", "ip",
         "parent", "1:0", "prio", "1", "u32", "match", "ip", "dst",
         str(prefix), "flowid", "1:3"],
        ["tc", "filter", "add", "dev", interface, "protocol", "ip",
         "parent", "1:0", "prio", "2", "u32", "match", "ip", "dst",
         plan["destination_node_ip"] + "/32", "flowid", "1:3"],
    ]
    return {"status": "W2_W3_ADAPTER_DRY_RUN_SCOPE_PASS" if current else
            "W2_W3_ADAPTER_DRY_RUN_BLOCKED",
        "current_target_path_proven": current,
        "source_node": "k8s-worker2", "destination_node": "k8s-worker3",
        "source_interface": interface, "target_prefix": str(prefix),
        "target_checkout_uid": checkout["uid"], "target_cart_uid": cart["uid"],
        "payment_worker1_uid": paid["uid"], "image_uid": image["uid"],
        "filter_preview_not_executed": preview,
        "cleanup_preview_not_executed": ["tc", "qdisc", "del", "dev",
            interface, "root"],
        "payment_control_pre_registered_for_this_canary": False,
        "filter_effect_on_shared_worker3_resources_proven": False,
        "two_qualified_controls": False,
        "fault_injected": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--topology", type=Path, required=True)
    parser.add_argument("--target-packets", type=Path, required=True)
    parser.add_argument("--payment-packets", type=Path, required=True)
    parser.add_argument("--route-scope", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    plan = yaml.safe_load(args.plan.read_text())
    topology, target, payment, paths = [read(path) for path in (
        args.topology, args.target_packets, args.payment_packets,
        args.route_scope)]
    cart = one(topology, "cart", "k8s-worker3")
    paid = one(topology, "payment", "k8s-worker1")
    live = {"target_route": route("ip", "-j", "route", "get", cart["ip"]),
        "payment_route": route("ip", "-j", "route", "get", paid["ip"]),
        "destination_node_route": route("ip", "-j", "route", "get",
            plan["destination_node_ip"]),
        "pod_cidr_route": route("ip", "-j", "route", "show",
            plan["destination_pod_cidr"]),
        "qdisc": route("tc", "-j", "qdisc", "show", "dev",
            plan["source_interface"])}
    value = decide(plan, topology, target, payment, paths, live)
    value["at_utc"] = datetime.now(timezone.utc).isoformat()
    value["live_routes_and_qdisc"] = live
    value["source_sha256"] = {str(path): sha(path) for path in (
        args.plan, args.topology, args.target_packets,
        args.payment_packets, args.route_scope)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"status": value["status"],
        "target": value["current_target_path_proven"],
        "fault_injected": False}))


if __name__ == "__main__":
    main()
