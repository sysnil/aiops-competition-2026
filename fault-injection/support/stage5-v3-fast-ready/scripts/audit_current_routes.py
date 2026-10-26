#!/usr/bin/env python3
"""Refresh the existing W2->W3 route audit with current SSH sockets."""

import importlib.util
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[2] / (
    "stage5-v3-collector-reliability/scripts/audit_route_scope.py")
spec = importlib.util.spec_from_file_location("audit_route_scope", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.HOSTS.update({
    "k8s-worker1": ("192.168.53.53", "/tmp/s5v3disk-w1.sock"),
    "k8s-worker2": ("192.168.53.54", "/tmp/s5v3disk-w2.sock"),
})
module.main()
