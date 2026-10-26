#!/usr/bin/env python3
"""Capture fresh public Kubernetes placement and EndpointSlice identity."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


RESOURCES = {
    "nodes": ["kubectl", "get", "nodes", "-o", "json"],
    "pods": ["kubectl", "-n", "otel-demo", "get", "pods", "-o", "json"],
    "services": ["kubectl", "-n", "otel-demo", "get", "services", "-o", "json"],
    "endpoint-slices": ["kubectl", "-n", "otel-demo", "get", "endpointslices", "-o", "json"],
    "deployments": ["kubectl", "-n", "otel-demo", "get", "deployments", "-o", "json"],
    "replica-sets": ["kubectl", "-n", "otel-demo", "get", "replicasets", "-o", "json"],
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    args.output.mkdir(parents=True)
    raw = {}
    hashes = {}
    for name, command in RESOURCES.items():
        text = subprocess.check_output(command, text=True, timeout=35)
        path = args.output / "raw" / (name + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text if text.endswith("\n") else text + "\n")
        raw[name] = json.loads(text)
        hashes[name] = sha(path)
    pods = {}
    by_service = defaultdict(list)
    for pod in raw["pods"]["items"]:
        meta, status = pod["metadata"], pod["status"]
        row = {"name": meta["name"], "uid": meta["uid"],
               "ip": status.get("podIP"), "node": pod["spec"].get("nodeName"),
               "ready": status.get("phase") == "Running" and bool(status.get("containerStatuses")) and
                   all(item.get("ready") for item in status.get("containerStatuses", [])),
               "container_ids": [item.get("containerID") for item in status.get("containerStatuses", [])],
               "runtime_image_ids": [item.get("imageID") for item in status.get("containerStatuses", [])],
               "owner_replicaset": next((item["name"] for item in meta.get("ownerReferences", [])
                                         if item.get("kind") == "ReplicaSet"), None)}
        pods[row["uid"]] = row
        service = meta["labels"].get("opentelemetry.io/name")
        if service:
            by_service[service].append(row)
    slices = defaultdict(list)
    for item in raw["endpoint-slices"]["items"]:
        service = item["metadata"]["labels"].get("kubernetes.io/service-name")
        for endpoint in item.get("endpoints") or []:
            ref = endpoint.get("targetRef") or {}
            slices[service].append({"addresses": endpoint.get("addresses"),
                "pod_uid": ref.get("uid"), "pod_name": ref.get("name"),
                "node": endpoint.get("nodeName"),
                "ready": (endpoint.get("conditions") or {}).get("ready")})
    nodes = {}
    for node in raw["nodes"]["items"]:
        nodes[node["metadata"]["name"]] = {"conditions": {
            row["type"]: row["status"] for row in node["status"]["conditions"]},
            "internal_ip": next((row["address"] for row in node["status"].get("addresses", [])
                                 if row["type"] == "InternalIP"), None)}
    result = {"status": "CURRENT_PUBLIC_TOPOLOGY_SNAPSHOT", "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_sha256": hashes, "nodes": nodes, "pods_by_uid": pods,
        "service_pods": {key: value for key, value in sorted(by_service.items())},
        "endpoint_slices": {key: value for key, value in sorted(slices.items())},
        "old_w3_to_w2_checkout_cart_membership": any(pod["node"] == "k8s-worker3"
            for pod in by_service.get("checkout", [])) and any(pod["node"] == "k8s-worker2"
            for pod in by_service.get("cart", [])),
        "private_gt_read": False}
    (args.output / "topology.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "nodes": len(nodes),
                      "pods": len(pods), "old_w3_to_w2_checkout_cart_membership": result["old_w3_to_w2_checkout_cart_membership"]}))


if __name__ == "__main__":
    main()
