#!/usr/bin/env python3
"""Build Stage4F pre-fault canonical entities from public metadata only."""

import argparse
import hashlib
import json
from pathlib import Path
import uuid

from network_path_precheck import validate as validate_network_path


def build(metadata, network_snapshot=None, public_case_id=None):
    if metadata.get('schema_version') != 1 or metadata.get('public_only') is not True:
        raise ValueError('Expected public metadata schema v1')
    case = metadata['case_id']
    services = set(metadata['services'])
    if not services:
        raise ValueError('No public services')
    nodes = {item['name'] for item in metadata['nodes'] if item.get('ready') is True}
    if not nodes:
        raise ValueError('No Ready public nodes')
    pods = {}
    for item in metadata['pods']:
        if not all(item.get(key) for key in ('uid', 'namespace', 'name', 'service', 'node')):
            raise ValueError('Incomplete public Pod identity')
        if item['uid'] in pods:
            raise ValueError('Duplicate Pod UID')
        if item['service'] not in services or item['node'] not in nodes:
            raise ValueError('Pod references absent service or node')
        pods[item['uid']] = item
    entities = [{'entity': f'SERVICE/{name}', 'layer': 'SERVICE'} for name in sorted(services)]
    entities.extend({'entity': f"POD/{item['namespace']}/{item['name']}", 'layer': 'POD',
                     'pod_uid': item['uid']} for item in sorted(pods.values(), key=lambda row: row['uid']))
    entities.extend({'entity': f'NODE/{name}', 'layer': 'NODE'} for name in sorted(nodes))
    relations = []
    for item in pods.values():
        pod = f"POD/{item['namespace']}/{item['name']}"
        relations.append({'source': f"SERVICE/{item['service']}", 'type': 'HAS_INSTANCE', 'target': pod})
        relations.append({'source': pod, 'type': 'RUNS_ON', 'target': f"NODE/{item['node']}"})
    calls = set()
    for item in metadata['service_calls']:
        source, destination = item['source'], item['destination']
        if source not in services or destination not in services:
            raise ValueError('Service call references absent service')
        calls.add(source + '->' + destination)
        relations.append({'source': f'SERVICE/{source}', 'type': 'CALLS',
                          'target': f'SERVICE/{destination}'})
    for item in metadata['pod_communications']:
        source = pods[item['source_pod_uid']]
        destination = pods[item['destination_pod_uid']]
        if source['service'] + '->' + destination['service'] not in calls:
            raise ValueError('Pod communication lacks public service call')
        relations.append({'source': f"POD/{source['namespace']}/{source['name']}",
                          'type': 'COMMUNICATES_WITH',
                          'target': f"POD/{destination['namespace']}/{destination['name']}"})
    if network_snapshot:
        path_result = validate_network_path(network_snapshot)
        path = path_result['network_path_entity']
        entities.append({'entity': path, 'layer': 'NETWORK_PATH'})
        for edge in path_result['traversing_edges']:
            source, destination = edge.split('->', 1)
            if edge not in calls:
                raise ValueError('Path membership references absent service call')
            relations.append({'source': f'SERVICE_EDGE/{source}/{destination}',
                              'type': 'TRAVERSES', 'target': path,
                              'route_proof': 'pre_fault_public_route_or_flow'})
    entity_ids = [item['entity'] for item in entities]
    if len(entity_ids) != len(set(entity_ids)):
        raise ValueError('Duplicate canonical entity')
    public_case_id = public_case_id or str(uuid.uuid4())
    if uuid.UUID(public_case_id).version != 4:
        raise ValueError('Public case ID must be UUIDv4')
    return {'schema_version': 1, 'public_only': True, 'case_id': case,
            'public_case_id': public_case_id,
            'candidates': entities, 'relations': relations,
            'candidate_counts': {layer: sum(item['layer'] == layer for item in entities)
                                 for layer in ('SERVICE', 'POD', 'NODE', 'NETWORK_PATH')}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--network-snapshot', type=Path)
    parser.add_argument('--public-case-id')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite pre-fault candidates')
    metadata = json.loads(args.metadata.read_text())
    network = json.loads(args.network_snapshot.read_text()) if args.network_snapshot else None
    result = build(metadata, network, args.public_case_id)
    result['input_sha256'] = {'metadata': hashlib.sha256(args.metadata.read_bytes()).hexdigest()}
    if args.network_snapshot:
        result['input_sha256']['network_snapshot'] = hashlib.sha256(args.network_snapshot.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'case_id': result['case_id'], 'candidate_counts': result['candidate_counts']}))


if __name__ == '__main__':
    main()
