#!/usr/bin/env python3
"""Prove specific public service edges on a fresh, fault-free node path."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

from convert_live import kubernetes_metadata, load_capture, trace_rows, write_json
from network_path_precheck import validate as validate_network_path


RTT = re.compile(r'time=([0-9]+(?:\.[0-9]+)?)\s*ms')
IFACE = re.compile(r'\bdev\s+([a-zA-Z0-9_.-]+)')


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def packet_count(pcap, source_ip, destination_ip):
    query = f'src host {source_ip} and dst host {destination_ip} and tcp'
    output = subprocess.run(['tcpdump', '-nn', '-r', str(pcap), query],
                            check=True, capture_output=True, text=True, timeout=30)
    return sum(' IP ' in line for line in output.stdout.splitlines())


def route_details(text, expected_next_hop=None):
    line = text.strip().splitlines()[0]
    interface = IFACE.search(line)
    if not interface:
        raise ValueError('Public route has no egress interface')
    via = re.search(r'\bvia\s+([0-9.]+)', line)
    next_hop = via.group(1) if via else None
    if expected_next_hop is not None and next_hop != expected_next_hop:
        raise ValueError('Observed route does not use the selected public next hop')
    if expected_next_hop is None and next_hop:
        raise ValueError('Control flow unexpectedly has a cross-node next hop')
    return {'egress_interface': interface.group(1), 'next_hop_ip': next_hop}


def build(before_dir, pcap, route_affected, route_control, ping_file,
          source_node, destination_node, traversing, control):
    capture = load_capture(before_dir, 'before')
    metadata = kubernetes_metadata(capture, 'readiness-route-proof')
    pods_by_service = {}
    for pod in metadata['pods']:
        pods_by_service.setdefault(pod['service'], []).append(pod)
    nodes = {item['metadata']['name']: item for item in
             capture['kubernetes']['nodes']['items']}
    if source_node not in nodes or destination_node not in nodes or (
            source_node == destination_node):
        raise ValueError('Selected path lacks two distinct public nodes')
    destination_ip = next((address['address'] for address in
                           nodes[destination_node]['status']['addresses']
                           if address['type'] == 'InternalIP'), None)
    if not destination_ip:
        raise ValueError('Selected destination node has no public InternalIP')
    affected_route = route_details(route_affected.read_text(), destination_ip)
    local_route = route_details(route_control.read_text())
    if affected_route['egress_interface'] == local_route['egress_interface']:
        raise ValueError('Selected path and control use the same egress interface')
    clean_rtt = [float(value) for value in RTT.findall(ping_file.read_text())]
    if len(clean_rtt) < 20:
        raise ValueError('Fewer than 20 pre-fault path RTT observations')
    _, edge_spans = trace_rows(capture, set(metadata['services']))
    path = f'NETWORK_PATH/{source_node}/{destination_node}'
    observed_at = datetime.fromtimestamp(pcap.stat().st_mtime, timezone.utc).isoformat()
    service_edges, route_evidence = [], []
    for edge, relation in [(name, 'TRAVERSES') for name in traversing] + [
            (control, 'CONTROL_NONTRAVERSING')]:
        if edge not in edge_spans or len(edge_spans[edge]) < 10:
            raise ValueError('Selected edge lacks ten clean public calls: ' + edge)
        source_service, destination_service = edge.split('->', 1)
        source_pods = pods_by_service.get(source_service, [])
        destination_pods = pods_by_service.get(destination_service, [])
        if len(source_pods) != 1 or len(destination_pods) != 1:
            raise ValueError('Selected service edge has ambiguous Pod endpoints: ' + edge)
        source_pod, destination_pod = source_pods[0], destination_pods[0]
        packets = packet_count(pcap, source_pod['pod_ip'], destination_pod['pod_ip'])
        if packets < 1:
            raise ValueError('No packet flow binds the selected edge to its Pods: ' + edge)
        if source_pod['node'] != source_node:
            raise ValueError('Selected flow does not originate on path source node')
        if relation == 'TRAVERSES':
            if destination_pod['node'] != destination_node:
                raise ValueError('Affected edge does not reach path destination node')
            decision = path
            route = affected_route
        else:
            if destination_pod['node'] != source_node:
                raise ValueError('Control is not a local-node path control')
            decision = 'LOCAL_NODE/' + source_node
            route = local_route
        proof = {'kind': 'pre_fault_flow_table', 'service_edge': edge,
                 'source_pod_uid': source_pod['uid'],
                 'destination_pod_uid': destination_pod['uid'],
                 'route_decision': decision,
                 'observed_at_utc': observed_at,
                 'flow_or_route_observation': {
                     'source_pod_ip': source_pod['pod_ip'],
                     'destination_pod_ip': destination_pod['pod_ip'],
                     'observed_tcp_packets': packets,
                     'clean_trace_calls': len(edge_spans[edge]),
                     'source_node_egress_interface': route['egress_interface'],
                     'next_hop_node': destination_node if relation == 'TRAVERSES' else None,
                     'packet_capture_sha256': sha256(pcap)}}
        proof_hash = hashlib.sha256(json.dumps(proof, sort_keys=True,
                                               separators=(',', ':')).encode()).hexdigest()
        evidence_id = 'public-flow-' + edge.replace('->', '-')
        route_evidence.append({'id': evidence_id, 'captured_before_fault': True,
                               'service_edge': edge,
                               'observed_at_utc': observed_at,
                               'source_pod_uid': source_pod['uid'],
                               'destination_pod_uid': destination_pod['uid'],
                               'route_decision': decision, 'proof_sha256': proof_hash,
                               'public_proof': proof})
        service_edges.append({'source_service': source_service,
                              'destination_service': destination_service,
                              'source_pod_name': source_pod['name'],
                              'destination_pod_name': destination_pod['name'],
                              'source_pod_uid': source_pod['uid'],
                              'destination_pod_uid': destination_pod['uid'],
                              'source_node': source_pod['node'],
                              'destination_node': destination_pod['node'],
                              'relation': relation,
                              'network_path_entity': decision,
                              'route_or_flow_evidence_id': evidence_id})
    snapshot = {'schema_version': 1, 'public_only': True,
                'network_path_entity': path, 'candidate_entities': [path],
                'nodes': metadata['nodes'],
                'pods': [{'uid': pod['uid'], 'name': pod['name'],
                          'service': pod['service'], 'node': pod['node'],
                          'pod_ip': pod['pod_ip']} for pod in metadata['pods']],
                'service_edges': service_edges, 'route_evidence': route_evidence,
                'normal_observations': {'path_latency_ms': clean_rtt},
                'source_evidence_sha256': {'pcap': sha256(pcap),
                                           'affected_route': sha256(route_affected),
                                           'control_route': sha256(route_control),
                                           'path_ping': sha256(ping_file),
                                           'capture_manifest': sha256(before_dir /
                                                                      'capture-manifest.json')}}
    validate_network_path(snapshot)
    return snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--before', required=True, type=Path)
    parser.add_argument('--pcap', required=True, type=Path)
    parser.add_argument('--affected-route', required=True, type=Path)
    parser.add_argument('--control-route', required=True, type=Path)
    parser.add_argument('--ping', required=True, type=Path)
    parser.add_argument('--source-node', required=True)
    parser.add_argument('--destination-node', required=True)
    parser.add_argument('--traversing', action='append', required=True)
    parser.add_argument('--control', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite public route proof')
    snapshot = build(args.before, args.pcap, args.affected_route,
                     args.control_route, args.ping, args.source_node,
                     args.destination_node, args.traversing, args.control)
    write_json(args.output, snapshot)
    result = validate_network_path(snapshot)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
