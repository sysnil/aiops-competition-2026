#!/usr/bin/env python3
"""Archive private injection truth and admission facts after one recovered case."""

import argparse
from datetime import datetime
import json
from pathlib import Path

import yaml

from contracts import sha256
from convert_live import (kubernetes_metadata, load_capture, load_network_probe,
                          metric_rows, read_json, write_json)
from freeze_preflight import verify as verify_preflight
from network_path_precheck import validate as validate_path
from pod_precheck import FAMILIES


EXPECTED_HOSTS = {'F01': {'k8s-worker1', 'k8s-worker2'},
                  'F02': {'k8s-worker2'}, 'F03': {'k8s-worker1'},
                  'F04': {'k8s-worker3'}}
NODE_METRICS = ('k8s_node_cpu_usage', 'k8s_node_cpu_time_seconds_total',
                'k8s_node_memory_available_bytes')


def duration(start, end):
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


def counts(rows, entity, families):
    return {family: len({row['time_unix_ns'] for row in rows
                         if row['entity'] == entity and row['family'] == family})
            for family in families}


def same_pod(pod, other):
    return bool(other and (pod['uid'], pod['node'], pod['container_ids']) == (
        other['uid'], other['node'], other['container_ids']) and
        pod['restart_counts'] == other['restart_counts'])


def build(root, case_id, before_dir, during_dir, after_dir, conversion_dir,
          recovery_file, fault_state_files, network_probe_file, output):
    if output.exists():
        raise ValueError('Refusing to overwrite a private case archive')
    verify_preflight(root, case_id)
    captures = {phase: load_capture(directory, phase) for phase, directory in (
        ('before', before_dir), ('during', during_dir), ('after', after_dir))}
    windows = {phase: item['window'] for phase, item in captures.items()}
    if duration(windows['after']['start_utc'], windows['after']['end_utc']) < 600:
        raise ValueError('Recovery observation is shorter than ten minutes')
    recovery = read_json(recovery_file)
    health_pass = recovery.get('status') == 'RECOVERY_GATE_PASS'
    preflight = root / 'preflight' / case_id
    snapshot = read_json(preflight / 'public-snapshot.json')
    candidate = read_json(preflight / 'candidate-universe.json')
    layer = {'F01': 'SERVICE', 'F02': 'POD', 'F03': 'NODE', 'F04': 'NETWORK_PATH'}[case_id]
    if case_id == 'F01':
        entity = 'SERVICE/' + snapshot['target_service']
    elif case_id == 'F02':
        pod = snapshot['target_pod']
        entity = f"POD/{pod['namespace']}/{pod['name']}"
    elif case_id == 'F03':
        entity = 'NODE/' + snapshot['target_node']
    else:
        entity = snapshot['network_path_entity']
    if entity not in {item['entity'] for item in candidate['candidates']}:
        raise ValueError('Private root is absent from the frozen public candidates')
    expected = EXPECTED_HOSTS[case_id]
    states = [read_json(path) for path in fault_state_files]
    if {state.get('host') for state in states} != expected or len(states) != len(expected):
        raise ValueError('Private fault states do not cover exactly the selected hosts')
    fault_durations = []
    for state in states:
        if state.get('case_id') != case_id or state.get('status') != 'CLEANED' or (
                not state.get('start_at_utc') or not state.get('cleanup_at_utc')):
            fault_durations.append(None)
        else:
            fault_durations.append(duration(state['start_at_utc'], state['cleanup_at_utc']))
    injection_success = all(value is not None and 295 <= value <= 310
                            for value in fault_durations)
    before = kubernetes_metadata(captures['before'], case_id)
    during = kubernetes_metadata(captures['during'], case_id)
    after = kubernetes_metadata(captures['after'], case_id)
    pod_maps = [{pod['name']: pod for pod in info['pods']} for info in (before, during, after)]
    rows = {phase: metric_rows(captures[phase], before['pods'])
            for phase in ('before', 'during')}
    quality = {'after_seconds': duration(windows['after']['start_utc'],
                                         windows['after']['end_utc']),
               'recovery_pass': health_pass,
               'injection_duration_seconds': fault_durations,
               'service_wide_replicas_stable': None,
               'target_node_ready_during': None,
               'pod_uid_before': None, 'pod_uid_during': None,
               'pod_container_id_before': None, 'pod_container_id_during': None,
               'pod_during_metric_sample_counts': {}, 'target_pod_recreated': None,
               'path_evidence_comparable': None}
    if case_id == 'F01':
        frozen_uids = set(read_json(preflight / 'service-precheck.json')['replica_uids'])
        replicas = [pod for pod in before['pods'] if pod['uid'] in frozen_uids]
        stable = len(replicas) == 2 and all(
            same_pod(pod, pod_maps[1].get(pod['name'])) and
            same_pod(pod, pod_maps[2].get(pod['name'])) for pod in replicas)
        quality['service_wide_replicas_stable'] = stable
        coverage = all(min(counts(rows[phase], f"POD/{pod['namespace']}/{pod['name']}",
                                  ('container_cpu_usage_seconds_total',)).values()) >= minimum
                       for pod in replicas for phase, minimum in (('before', 20), ('during', 10)))
    elif case_id == 'F02':
        frozen = snapshot['target_pod']
        name = frozen['name']
        prior, current, recovered = (mapping.get(name) for mapping in pod_maps)
        quality['pod_uid_before'] = prior['uid'] if prior else None
        quality['pod_uid_during'] = current['uid'] if current else None
        quality['pod_container_id_before'] = frozen['container_id']
        quality['pod_container_id_during'] = (next(iter(current['container_ids'].values()))
                                              if current and len(current['container_ids']) == 1 else None)
        quality['target_pod_recreated'] = not (prior and same_pod(prior, current) and
                                               same_pod(prior, recovered))
        entity_pod = f"POD/{frozen['namespace']}/{name}"
        for phase in ('before', 'during'):
            observed = counts(rows[phase], entity_pod, FAMILIES)
            if phase == 'during':
                quality['pod_during_metric_sample_counts'] = observed
        coverage = (not quality['target_pod_recreated'] and
                    min(counts(rows['before'], entity_pod, FAMILIES).values()) >= 20 and
                    min(quality['pod_during_metric_sample_counts'].values()) >= 10)
    elif case_id == 'F03':
        quality['target_node_ready_during'] = any(
            node['name'] == snapshot['target_node'] and node['ready']
            for node in during['nodes'])
        node_entity = 'NODE/' + snapshot['target_node']
        coverage = (quality['target_node_ready_during'] and
                    all(min(counts(rows[phase], node_entity, NODE_METRICS).values()) >= minimum
                        for phase, minimum in (('before', 20), ('during', 10))))
    else:
        if not network_probe_file:
            raise ValueError('F04 private quality requires both network probe phases')
        proof = snapshot
        validate_path(proof)
        probe = load_network_probe(network_probe_file, captures, proof)
        current_uids = {pod['uid'] for pod in after['pods']}
        frozen_uids = {edge[prefix + '_pod_uid'] for edge in proof['service_edges']
                       for prefix in ('source', 'destination')}
        quality['path_evidence_comparable'] = (probe['path'] == entity and
                                               frozen_uids <= current_uids)
        coverage = quality['path_evidence_comparable']
    bundle = read_json(conversion_dir / 'public/variant-b/bundle.json')
    if bundle['public_case_id'] != candidate['public_case_id']:
        raise ValueError('Private archive and public inference UUID differ')
    direct = [{'modality': modality, 'family': signal['family']}
              for modality, signals in bundle.get('signals', {}).get(entity, {}).items()
              for signal in signals if signal.get('observed') is True and
              signal.get('abnormal') is True]
    quality['observability_pass'] = bool(coverage and direct)
    propagated = [{'entity': name, 'family': signal['family']}
                  for name, modalities in bundle.get('signals', {}).items() if name != entity
                  for signal in modalities.get('traces', [])
                  if signal.get('observed') is True and signal.get('abnormal') is True]
    controls = (read_json(preflight / 'network-path-precheck.json')['control_edges']
                if case_id == 'F04' else
                [pod['name'] for pod in before['pods'] if pod['service'] == 'ad' and
                 pod['uid'] != snapshot.get('target_pod', {}).get('uid')]
                if case_id == 'F02' else
                [item['entity'] for item in candidate['candidates']
                 if item['layer'] == 'NODE' and item['entity'] != entity] if case_id == 'F03'
                else ['frontend->recommendation'])
    gt = {'schema_version': 1, 'case_id': case_id, 'root_layer': layer,
          'root_entity': entity, 'injection_success': injection_success,
          'target_pod_uid': snapshot.get('target_pod', {}).get('uid'),
          'direct_effects': direct, 'propagated_effects': propagated,
          'controls': controls,
          'private_injection_evidence': [{'host': state['host'], 'sha256': sha256(path)}
                                         for state, path in zip(states, fault_state_files)],
          'fault_duration_seconds': fault_durations}
    output.mkdir(parents=True)
    (output / 'ground_truth.yaml').write_text(yaml.safe_dump(gt, sort_keys=False))
    write_json(output / 'admission-quality.json', quality)
    write_json(output / 'private-evidence-manifest.json', {
        'before_capture_sha256': sha256(before_dir / 'capture-manifest.json'),
        'during_capture_sha256': sha256(during_dir / 'capture-manifest.json'),
        'after_capture_sha256': sha256(after_dir / 'capture-manifest.json'),
        'recovery_gate_sha256': sha256(recovery_file),
        'fault_state_sha256': {path.name: sha256(path) for path in fault_state_files},
        'public_bundle_sha256': sha256(conversion_dir / 'public/variant-b/bundle.json')})
    return {'status': 'PRIVATE_CASE_EVIDENCE_ARCHIVED', 'case_id': case_id,
            'injection_success': injection_success,
            'observability_pass': quality['observability_pass'],
            'recovery_pass': health_pass}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--case-id', choices=tuple(EXPECTED_HOSTS), required=True)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--during', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--conversion', type=Path, required=True)
    parser.add_argument('--recovery', type=Path, required=True)
    parser.add_argument('--fault-state', action='append', type=Path, required=True)
    parser.add_argument('--network-probe', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.case_id, args.before, args.during,
                           args.after, args.conversion, args.recovery,
                           args.fault_state, args.network_probe, args.output)))


if __name__ == '__main__':
    main()
