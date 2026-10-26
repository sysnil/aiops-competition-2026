#!/usr/bin/env python3
"""Validate public, pre-fault edge-to-NetworkPath membership evidence."""

import argparse
import hashlib
import json
from pathlib import Path
import re


FORBIDDEN = re.compile(r'\b(?:VM1|gretap|netem|fault[_ -]?injection[_ -]?port|delay[_ -]?parameter)\b',
                       re.IGNORECASE)
PROOF_HASH = re.compile(r'^[0-9a-f]{64}$')


def validate(snapshot):
    if snapshot.get('schema_version') != 1 or snapshot.get('public_only') is not True:
        raise ValueError('Expected public network snapshot schema v1')
    if FORBIDDEN.search(json.dumps(snapshot, ensure_ascii=False)):
        raise ValueError('Injection-specific network term in public topology')
    path = snapshot['network_path_entity']
    if not path.startswith('NETWORK_PATH/') or path not in snapshot['candidate_entities']:
        raise ValueError('NetworkPath is absent from pre-fault public candidates')
    _, source_node, destination_node = path.split('/', 2)
    nodes = {item['name'] for item in snapshot['nodes'] if item.get('ready') is True}
    if source_node not in nodes or destination_node not in nodes:
        raise ValueError('NetworkPath endpoint node is not Ready')
    pods = {item['uid']: item for item in snapshot['pods']}
    if len(pods) != len(snapshot['pods']):
        raise ValueError('Duplicate Pod UID in public placement')
    proofs = {item['id']: item for item in snapshot['route_evidence']}
    if len(proofs) != len(snapshot['route_evidence']):
        raise ValueError('Duplicate route evidence ID')
    membership = {}
    for edge in snapshot['service_edges']:
        edge_id = edge['source_service'] + '->' + edge['destination_service']
        if edge_id in membership:
            raise ValueError('Duplicate service edge: ' + edge_id)
        source = pods.get(edge['source_pod_uid'])
        destination = pods.get(edge['destination_pod_uid'])
        if not source or not destination:
            raise ValueError('Service edge references unknown Pod UID: ' + edge_id)
        if source['service'] != edge['source_service'] or destination['service'] != edge['destination_service']:
            raise ValueError('Service-to-Pod relationship disagrees: ' + edge_id)
        if source['node'] != edge['source_node'] or destination['node'] != edge['destination_node']:
            raise ValueError('Pod-to-Node relationship disagrees: ' + edge_id)
        evidence = proofs.get(edge['route_or_flow_evidence_id'])
        if not evidence or evidence.get('captured_before_fault') is not True:
            raise ValueError('Missing pre-fault route proof: ' + edge_id)
        if evidence['service_edge'] != edge_id or evidence['source_pod_uid'] != source['uid'] or (
                evidence['destination_pod_uid'] != destination['uid']):
            raise ValueError('Route proof is not bound to this edge and Pod pair: ' + edge_id)
        if not PROOF_HASH.fullmatch(evidence.get('proof_sha256', '')):
            raise ValueError('Route proof lacks a SHA-256 digest: ' + edge_id)
        proof = evidence.get('public_proof')
        if not isinstance(proof, dict) or proof.get('kind') not in (
                'pre_fault_route_table', 'pre_fault_flow_table', 'pre_fault_packet_path_probe'):
            raise ValueError('Route proof lacks public observable content: ' + edge_id)
        proof_bytes = json.dumps(proof, sort_keys=True, separators=(',', ':')).encode()
        if hashlib.sha256(proof_bytes).hexdigest() != evidence['proof_sha256']:
            raise ValueError('Public route proof digest mismatch: ' + edge_id)
        if (proof.get('service_edge') != edge_id or proof.get('source_pod_uid') != source['uid'] or
                proof.get('destination_pod_uid') != destination['uid'] or
                proof.get('route_decision') != evidence['route_decision'] or
                not proof.get('flow_or_route_observation')):
            raise ValueError('Public route proof content is not bound to this edge: ' + edge_id)
        if edge['relation'] == 'TRAVERSES':
            if (edge['network_path_entity'] != path or source['node'] != source_node or
                    destination['node'] != destination_node or evidence['route_decision'] != path):
                raise ValueError('TRAVERSES claim is not proved for path: ' + edge_id)
        elif edge['relation'] == 'CONTROL_NONTRAVERSING':
            if edge['network_path_entity'] == path or evidence['route_decision'] == path:
                raise ValueError('Control edge traverses the selected path: ' + edge_id)
        else:
            raise ValueError('Unknown path membership relation: ' + edge['relation'])
        membership[edge_id] = edge['relation']
    traversing = [edge for edge, relation in membership.items() if relation == 'TRAVERSES']
    controls = [edge for edge, relation in membership.items() if relation == 'CONTROL_NONTRAVERSING']
    if len(traversing) < 2 or not controls:
        raise ValueError('Need at least two traversing edges and one nontraversing control')
    normal = snapshot.get('normal_observations', {})
    latency = normal.get('path_latency_ms', [])
    if len(latency) < 20 or any(not isinstance(value, (int, float)) or value < 0 for value in latency):
        raise ValueError('Insufficient clean pre-fault NetworkPath latency samples')
    affected_names = {name for edge in traversing for name in edge.split('->')}
    if not any(any(name in affected_names for name in edge.split('->')) for edge in controls):
        raise ValueError('Control edge must share a source or destination service')
    return {'status': 'NETWORK_PATH_PRECHECK_PASS', 'network_path_entity': path,
            'traversing_edges': traversing, 'control_edges': controls,
            'route_evidence_count': len(proofs)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--public-snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite network path precheck')
    snapshot = json.loads(args.public_snapshot.read_text())
    result = validate(snapshot)
    result['input_sha256'] = hashlib.sha256(args.public_snapshot.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(result['status'])


if __name__ == '__main__':
    main()
