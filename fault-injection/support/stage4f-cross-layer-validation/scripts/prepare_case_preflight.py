#!/usr/bin/env python3
"""Build one case's frozen-candidate inputs from a fresh clean capture only."""

import argparse
import hashlib
import json
from pathlib import Path

from contracts import sha256
from convert_live import (kubernetes_metadata, load_capture, metric_rows,
                          read_json, write_json)
from network_path_precheck import validate as validate_path
from pod_precheck import FAMILIES, validate as validate_pod
from service_node_precheck import node as validate_node, service as validate_service


VALIDATORS = {'F01': ('service-precheck.json', validate_service),
              'F02': ('pod-precheck.json', validate_pod),
              'F03': ('node-precheck.json', validate_node),
              'F04': ('network-path-precheck.json', validate_path)}


def stable_deployment(capture, service):
    item = next((item for item in capture['kubernetes']['deployments']['items']
                 if item['metadata']['name'] == service), None)
    if not item:
        return False
    desired = item['spec']['replicas']
    return (item['metadata']['generation'] == item['status'].get('observedGeneration') and
            item['status'].get('readyReplicas') == desired and
            item['status'].get('updatedReplicas') == desired)


def metric_counts(rows, entity, family):
    matching = [row for row in rows if row['entity'] == entity and row['family'] == family]
    return len({row['time_unix_ns'] for row in matching})


def service_snapshot(capture, metadata, rows, candidates, service):
    if not service:
        raise ValueError('F01 target service is required')
    pods = [pod for pod in metadata['pods'] if pod['service'] == service]
    observed = {}
    for pod in pods:
        entity = f"POD/{pod['namespace']}/{pod['name']}"
        count = metric_counts(rows, entity, 'container_cpu_usage_seconds_total')
        observed[pod['uid']] = {'cpu_usage': count}
    return {'schema_version': 1, 'public_only': True, 'target_service': service,
            'candidate_entities': candidates, 'rollout_pending': not stable_deployment(
                capture, service), 'before_window': capture['window'],
            'pods': [{'uid': pod['uid'], 'service': pod['service'], 'ready': True,
                      'stable_through_before': observed[pod['uid']]['cpu_usage'] >= 20}
                     for pod in pods], 'pod_metric_sample_counts': observed}


def pod_snapshot(capture, metadata, rows, candidates, pod_name):
    pod = next((pod for pod in metadata['pods'] if pod['name'] == pod_name), None)
    if not pod or len(pod['container_ids']) != 1:
        raise ValueError('F02 target Pod is absent or has ambiguous containers')
    container_id = next(iter(pod['container_ids'].values()))
    entity = f"POD/{pod['namespace']}/{pod['name']}"
    series = {}
    for family in FAMILIES:
        selected = [row for row in rows if row['entity'] == entity and row['family'] == family]
        if len({row['series_id'] for row in selected}) != 1:
            raise ValueError('F02 baseline metric series is not container-stable: ' + family)
        series[family] = {'container_id': container_id,
                          'samples': [{'time_unix_ns': row['time_unix_ns'],
                                       'value': row['value']} for row in selected]}
    first = min(item['time_unix_ns'] for item in series[FAMILIES[0]]['samples'])
    last = max(item['time_unix_ns'] for item in series[FAMILIES[0]]['samples'])
    identity = {'namespace': pod['namespace'], 'name': pod['name'], 'uid': pod['uid'],
                'node': pod['node'], 'container_id': container_id}
    observations = [{**identity, 'observed_unix_ns': stamp, 'ready': True}
                    for stamp in (first, last)]
    return {'schema_version': 1, 'public_only': True,
            'before_window': capture['window'], 'scrape_interval_seconds': 15,
            'target_pod': identity, 'candidate_entities': candidates,
            'rollout_pending': not stable_deployment(capture, pod['service']),
            'pod_observations': observations, 'metric_series': series}


def node_snapshot(capture, metadata, rows, candidates, node_name):
    if not node_name:
        raise ValueError('F03 target node is required')
    business = [pod for pod in metadata['pods'] if pod['node'] == node_name and
                pod['service'] not in {'flagd', 'load-generator', 'otel-collector',
                                       'otel-cluster-collector'}]
    metric_names = {'cpu_usage': 'k8s_node_cpu_usage',
                    'cpu_time': 'k8s_node_cpu_time_seconds_total',
                    'memory_available': 'k8s_node_memory_available_bytes'}
    counts = {name: metric_counts(rows, 'NODE/' + node_name, family)
              for name, family in metric_names.items()}
    return {'schema_version': 1, 'public_only': True,
            'target_node': node_name, 'candidate_entities': candidates,
            'node_ready_before': any(item['name'] == node_name and item['ready']
                                     for item in metadata['nodes']),
            'rollout_pending': any(not stable_deployment(capture, pod['service'])
                                   for pod in business),
            'before_window': capture['window'],
            'pods': [{'node': pod['node'], 'service': pod['service'], 'ready': True,
                      'business_service': True} for pod in business],
            'node_metric_sample_counts': counts}


def path_snapshot(metadata, candidates, proof):
    if proof is None:
        raise ValueError('F04 verified route proof is required')
    current = {pod['uid']: pod for pod in metadata['pods']}
    for edge in proof['service_edges']:
        for prefix in ('source', 'destination'):
            pod = current.get(edge[prefix + '_pod_uid'])
            if (not pod or pod['service'] != edge[prefix + '_service'] or
                    pod['node'] != edge[prefix + '_node']):
                raise ValueError('Frozen route proof Pod UID or placement changed')
    snapshot = dict(proof)
    snapshot['candidate_entities'] = candidates
    return snapshot


def build(case_id, before_dir, clean_conversion, network_proof, output,
          target_service=None, target_pod=None, target_node=None):
    if case_id not in VALIDATORS or output.exists():
        raise ValueError('Unknown case or existing preflight directory')
    capture = load_capture(before_dir, 'before')
    conversion = read_json(clean_conversion / 'conversion-manifest.json')
    if (conversion.get('status') != 'STAGE4F_LIVE_CONVERSION_COMPLETE' or
            conversion['input_capture_sha256']['before'] !=
            sha256(before_dir / 'capture-manifest.json')):
        raise ValueError('Clean conversion was not built from this before capture')
    candidate_file = clean_conversion / 'candidate-universe.json'
    if conversion['output_sha256'].get('candidate-universe.json') != sha256(candidate_file):
        raise ValueError('Clean public candidate output hash changed')
    candidate_record = read_json(candidate_file)
    if candidate_record['case_id'] != case_id or candidate_record['public_only'] is not True:
        raise ValueError('Clean conversion belongs to another case')
    candidates = [item['entity'] for item in candidate_record['candidates']]
    calibration = {'public_only': True, 'case_id': case_id, 'variants': {}}
    for variant in ('A', 'B'):
        name = f'public/variant-{variant.lower()}/bundle.json'
        bundle_file = clean_conversion / name
        if conversion['output_sha256'].get(name) != sha256(bundle_file):
            raise ValueError('Clean Variant ' + variant + ' output hash changed')
        bundle = read_json(bundle_file)
        if bundle['public_case_id'] != candidate_record['public_case_id']:
            raise ValueError('Clean candidate UUID differs across variants')
        calibration['variants'][variant] = {
            'clean_null_window_maxima': bundle['clean_null_window_maxima'],
            'clean_null_pagerank_maxima': bundle['clean_null_pagerank_maxima']}
    metadata = kubernetes_metadata(capture, case_id)
    rows = metric_rows(capture, metadata['pods'])
    proof = read_json(network_proof) if network_proof else None
    if case_id == 'F01':
        snapshot = service_snapshot(capture, metadata, rows, candidates, target_service)
    elif case_id == 'F02':
        snapshot = pod_snapshot(capture, metadata, rows, candidates, target_pod)
    elif case_id == 'F03':
        snapshot = node_snapshot(capture, metadata, rows, candidates, target_node)
    else:
        snapshot = path_snapshot(metadata, candidates, proof)
    precheck_name, validator = VALIDATORS[case_id]
    precheck = validator(snapshot)
    output.mkdir(parents=True)
    snapshot_file = output / 'public-snapshot.json'
    write_json(snapshot_file, snapshot)
    precheck['input_sha256'] = sha256(snapshot_file)
    write_json(output / precheck_name, precheck)
    write_json(output / 'candidate-universe.json', candidate_record)
    write_json(output / 'clean-null-calibration.json', calibration)
    write_json(output / 'source-inputs.json', {
        'before_capture_manifest_sha256': sha256(before_dir / 'capture-manifest.json'),
        'clean_conversion_manifest_sha256': sha256(clean_conversion /
                                                    'conversion-manifest.json'),
        'network_proof_sha256': sha256(network_proof) if network_proof else None})
    return {'status': precheck['status'], 'case_id': case_id,
            'public_case_id': candidate_record['public_case_id'],
            'candidates': len(candidates)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case-id', choices=tuple(VALIDATORS), required=True)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--clean-conversion', type=Path, required=True)
    parser.add_argument('--network-proof', type=Path)
    parser.add_argument('--target-service')
    parser.add_argument('--target-pod')
    parser.add_argument('--target-node')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.case_id, args.before, args.clean_conversion,
                           args.network_proof, args.output, args.target_service,
                           args.target_pod, args.target_node)))


if __name__ == '__main__':
    main()
