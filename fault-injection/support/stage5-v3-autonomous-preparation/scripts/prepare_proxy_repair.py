#!/usr/bin/env python3
"""Prepare and validate a reversible Envoy frontend upstream idle-timeout trial."""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import yaml


def command(*args, input_data=None):
    result = subprocess.run(args, input=input_data, capture_output=True,
                            check=True, timeout=45)
    return result.stdout


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(data)
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("A repair trial needs a new directory")
    args.output.mkdir(parents=True)
    pod = command("kubectl", "-n", "otel-demo", "get", "pods", "-l",
                  "opentelemetry.io/name=frontend-proxy", "-o", "json")
    items = json.loads(pod)["items"]
    if len(items) != 1 or not all(s.get("ready") for s in
                                 items[0]["status"].get("containerStatuses", [])):
        raise ValueError("Proxy Pod is not a unique Ready target")
    name = items[0]["metadata"]["name"]
    original = command("kubectl", "-n", "otel-demo", "exec", name, "--",
                       "cat", "/home/envoy/envoy.tmpl.yaml")
    deployment = command("kubectl", "-n", "otel-demo", "get", "deployment",
                         "frontend-proxy", "-o", "json")
    source = yaml.safe_load(original)
    changed = copy.deepcopy(source)
    clusters = [row for row in changed["static_resources"]["clusters"]
                if row["name"] == "frontend"]
    if len(clusters) != 1 or "typed_extension_protocol_options" in clusters[0]:
        raise ValueError("Unexpected existing frontend upstream protocol options")
    clusters[0]["typed_extension_protocol_options"] = {
        "envoy.extensions.upstreams.http.v3.HttpProtocolOptions": {
            "@type": "type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions",
            "common_http_protocol_options": {
                "idle_timeout": "2s", "max_requests_per_connection": 10},
            "explicit_http_config": {"http_protocol_options": {}}}}
    rendered = yaml.safe_dump(changed, sort_keys=False).encode()
    if yaml.safe_load(rendered) != changed:
        raise ValueError("YAML round trip changed repaired configuration")
    original_hash = write_new(args.output / "original-envoy.tmpl.yaml", original)
    deployment_hash = write_new(args.output / "original-deployment.json", deployment)
    pod_hash = write_new(args.output / "original-pod.json", pod)
    repaired_hash = write_new(args.output / "frontend-proxy-envoy-c2.tmpl.yaml", rendered)
    manifest = {"status": "PREPARED_NOT_APPLIED", "at_utc":
        datetime.now(timezone.utc).isoformat(), "pod": name,
        "pod_uid": items[0]["metadata"]["uid"],
        "old_revision": json.loads(deployment)["metadata"]["annotations"]
            ["deployment.kubernetes.io/revision"],
        "change": "frontend upstream idle_timeout 2s and max 10 requests/connection; backend advertises 5s",
        "sha256": {"original_template": original_hash,
                   "repaired_template": repaired_hash,
                   "original_deployment": deployment_hash,
                   "original_pod": pod_hash}}
    write_new(args.output / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
