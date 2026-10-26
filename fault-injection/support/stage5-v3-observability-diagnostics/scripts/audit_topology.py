"""Read-only post-rollout Pod/EndpointSlice and directional Calico route audit."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


MASTER = ("192.168.53.52", "/tmp/stage4f-formal-ssh")
WORKERS = {
    "k8s-worker1": ("192.168.53.53", "/tmp/s5v3-worker1-20261012.sock"),
    "k8s-worker2": ("192.168.53.54", "/tmp/s5v3-worker2-20261012.sock"),
    "k8s-worker3": ("192.168.53.55", "/tmp/s5v3-worker3-20261012.sock"),
}
EDGES = (("frontend-proxy", "image-provider"),
         ("frontend-proxy", "frontend"),
         ("frontend", "cart"),
         ("frontend", "product-catalog"),
         ("checkout", "cart"),
         ("checkout", "shipping"),
         ("shipping", "quote"))


def remote(address, socket, *args, timeout=25):
    result = subprocess.run(["ssh", "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5", "-o", "ControlPath=" + socket,
        "root@" + address, *args], capture_output=True, text=True,
        timeout=timeout)
    if result.returncode:
        raise RuntimeError(address + " read-only command failed: " +
                           result.stderr[-400:])
    return result.stdout


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def ready_pods(raw):
    result = {}
    for pod in json.loads(raw)["items"]:
        if (pod["metadata"].get("deletionTimestamp") is not None or
                pod["status"].get("phase") != "Running" or
                not pod["status"].get("containerStatuses") or
                not all(row.get("ready") for row in pod["status"][
                    "containerStatuses"])):
            continue
        name = pod["metadata"]["labels"].get("opentelemetry.io/name")
        if name:
            result.setdefault(name, []).append({"name": pod["metadata"]["name"],
                "uid": pod["metadata"]["uid"],
                "ip": pod["status"].get("podIP"),
                "node": pod["spec"].get("nodeName")})
    return result


def endpoints(raw):
    result = {}
    for item in json.loads(raw)["items"]:
        service = item["metadata"]["labels"].get("kubernetes.io/service-name")
        if not service:
            continue
        for edge in item.get("endpoints") or []:
            if edge.get("conditions", {}).get("ready") is False:
                continue
            for ip in edge.get("addresses") or []:
                result.setdefault(service, {})[ip] = edge.get("targetRef", {}).get(
                    "uid")
    return result


def route(source, destination):
    address, socket = WORKERS[source["node"]]
    raw = remote(address, socket, "ip", "route", "get", destination["ip"],
                 timeout=10).strip()
    if source["node"] == destination["node"]:
        verified = " dev cali" in raw and " dev ens160" not in raw
        directional_path = "SAME_NODE"
    else:
        destination_node_ip = WORKERS[destination["node"]][0]
        verified = ("via " + destination_node_ip in raw and " dev ens160" in raw)
        directional_path = "NETWORK_PATH/" + source["node"] + "/" + \
            destination["node"]
    return {"source_pod_uid": source["uid"],
        "destination_pod_uid": destination["uid"],
        "source_node": source["node"], "destination_node": destination["node"],
        "route": raw, "route_sha256": sha(raw),
        "directional_path": directional_path,
        "route_verified": verified,
        "business_packet_proof_in_this_audit": False,
        "formal_TRAVERSES_proven": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    pods_raw = remote(*MASTER, "kubectl", "-n", "otel-demo", "get", "pods",
                      "-o", "json")
    slices_raw = remote(*MASTER, "kubectl", "-n", "otel-demo", "get",
                        "endpointslices", "-o", "json")
    pods = ready_pods(pods_raw)
    slices = endpoints(slices_raw)
    rows = []
    for source_service, destination_service in EDGES:
        for source in pods.get(source_service, []):
            for destination in pods.get(destination_service, []):
                result = route(source, destination)
                result.update({"edge": source_service + "->" +
                               destination_service,
                               "source_pod_name": source["name"],
                               "destination_pod_name": destination["name"],
                               "endpoint_slice_uid_matches": slices.get(
                                   destination_service, {}).get(destination[
                                       "ip"]) == destination["uid"]})
                rows.append(result)
    expected = sum(len(pods.get(a, [])) * len(pods.get(b, [])) for a, b in EDGES)
    value = {"status": "POST_ROLLOUT_ROUTE_METADATA_PASS" if rows and len(
        rows) == expected and all(row["route_verified"] and row[
            "endpoint_slice_uid_matches"] for row in rows) else
        "POST_ROLLOUT_ROUTE_METADATA_PARTIAL",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "pods_sha256": sha(pods_raw),
        "endpoint_slices_sha256": sha(slices_raw),
        "pod_mapping": pods, "edges": rows,
        "old_W3_to_W2_checkout_cart_membership_retained": any(row[
            "edge"] == "checkout->cart" and row["directional_path"] ==
            "NETWORK_PATH/k8s-worker3/k8s-worker2" for row in rows),
        "cart_control_independent_of_W3_to_W2_return_path": False,
        "formal_path_proof_status": "UNVERIFIED_NO_FRESH_BUSINESS_PACKET",
        "private_gt_read": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": value["status"], "edges": len(rows),
                      "old_target_retained": value[
                          "old_W3_to_W2_checkout_cart_membership_retained"]}))


if __name__ == "__main__":
    main()
