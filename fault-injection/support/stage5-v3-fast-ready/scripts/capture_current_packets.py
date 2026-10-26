#!/usr/bin/env python3
"""Use the existing bounded packet proof with this session's SSH sockets."""

import importlib.util
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[2] / (
    "stage5-v3-collector-reliability/scripts/capture_packet_proof.py")
spec = importlib.util.spec_from_file_location("capture_packet_proof", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.HOSTS.update({
    "k8s-worker1": ("192.168.53.53", "/tmp/s5v3disk-w1.sock"),
    "k8s-worker2": ("192.168.53.54", "/tmp/s5v3disk-w2.sock"),
    "k8s-worker3": ("192.168.53.55", "/tmp/s5v3disk-w3.sock"),
})
module.main()
