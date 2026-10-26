#!/usr/bin/env python3
"""Lint, freeze and verify Stage4F pre-fault contracts and executable code."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import yaml


CONTRACTS = (
    'candidate-contract.yaml',
    'observation-contract.yaml',
    'scoring-contract.yaml',
    'network-path-contract.yaml',
    'case-validation-contract.yaml',
    'case-plan.yaml',
)
LAYERS = ['SERVICE', 'POD', 'NODE', 'NETWORK_PATH']
ROUTE_PROOF = Path('readiness/f04-route-proof-podprobe-2026-10-03/public_verified_route_map.json')
POD_METRICS = [
    'container_cpu_usage_seconds_total',
    'container_memory_working_set_bytes',
    'container_cpu_cfs_periods_total',
    'container_cpu_cfs_throttled_periods_total',
    'container_cpu_cfs_throttled_seconds_total',
]
NODE_METRICS = ['k8s_node_cpu_usage', 'k8s_node_cpu_time_seconds_total',
                'k8s_node_memory_available_bytes']


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def lint(root):
    design = root / 'design'
    data = {}
    for name in CONTRACTS:
        path = design / name
        if not path.is_file():
            raise ValueError('Missing design file: ' + str(path))
        item = yaml.safe_load(path.read_text())
        if not isinstance(item, dict) or item.get('schema_version') != 1:
            raise ValueError('Invalid schema version: ' + name)
        data[name] = item
    candidates = data['candidate-contract.yaml']
    observations = data['observation-contract.yaml']
    scoring = data['scoring-contract.yaml']
    paths = data['network-path-contract.yaml']
    admission = data['case-validation-contract.yaml']
    plan = data['case-plan.yaml']
    if candidates['candidate_layers'] != LAYERS or scoring['ranked_entity_levels'] != LAYERS:
        raise ValueError('Candidate and scoring layer universes disagree')
    if set(candidates['relationships']) != {
        'HAS_INSTANCE', 'RUNS_ON', 'CALLS', 'COMMUNICATES_WITH', 'TRAVERSES'}:
        raise ValueError('Missing or extra canonical relationship')
    windows = observations['windows']
    if observations['pod_required_metric_families'] != POD_METRICS:
        raise ValueError('F02 must require all five distinct CPU, memory and CFS series')
    if observations['node_required_metric_families'] != NODE_METRICS:
        raise ValueError('F03 must require three node-labeled CPU/memory series')
    if observations.get('trace_acquisition', {}).get('source') != 'JAEGER_OPENSEARCH_STORAGE':
        raise ValueError('Stage4F trace acquisition source is not fixed before freeze')
    if (windows['before_min_seconds'] < 600 or windows['during_seconds'] != 300 or
            windows['after_min_seconds'] < 600 or windows['inference_phases'] != ['before', 'during']):
        raise ValueError('Stage4F observation windows violate the design')
    if not paths['edge_membership_proof']['required_before_fault']:
        raise ValueError('Network path membership must be proven before the fault')
    if paths['path_latency_probe_target'] != 'verified_destination_pod_ip':
        raise ValueError('Network probes must target a verified destination Pod IP')
    if paths['variant_a']['includes_network_path_mapping'] or not paths['variant_b']['includes_network_path_mapping']:
        raise ValueError('Variant A/B network boundaries disagree')
    if scoring['hierarchical_report']['scalar_weighted_sum_as_primary']:
        raise ValueError('Weighted hierarchy sum cannot be the sole primary metric')
    eligibility = scoring['method_eligibility']
    if eligibility['mandatory_for_executable_readiness'] != [
            'OpenRCA-XL', 'CrossLayer-MicroRCA', 'CrossLayer-SimpleRCA'] or (
            eligibility['nonmandatory_when_label_free_inference_unavailable'] != [
                'MicroIRC-original', 'MicroIRC-XL']):
        raise ValueError('Stage4F method eligibility changed')
    if scoring['simple_rca_calibration']['minimum_independent_signal_families'] < 4:
        raise ValueError('SimpleRCA minimum evidence is too small')
    if (scoring['simple_rca_calibration']['clean_null_resource_min_samples'] != 4 or
            scoring['simple_rca_calibration']['clean_null_trace_min_spans'] != 6 or
            scoring['simple_rca_calibration']['clean_null_network_packets_min'] != 20):
        raise ValueError('Clean-null sample policy differs from frozen conversion code')
    if scoring['simple_rca_calibration']['null_windows_minimum'] > (
            windows['before_min_seconds'] // scoring['simple_rca_calibration']['clean_null_window_seconds']):
        raise ValueError('Clean baseline cannot supply the required null windows')
    if admission['case_ids'] != list(plan['primary_cases']):
        raise ValueError('Primary case plan and admission case IDs disagree')
    if [plan['primary_cases'][case]['layer'] for case in admission['case_ids']] != LAYERS:
        raise ValueError('F01-F04 do not cover the four layers in order')
    if [plan['primary_cases'][case]['execution_adapter'] for case in admission['case_ids']] != [
            'host_fault.py', 'pod_cpu_exec.py', 'host_fault.py', 'host_fault.py']:
        raise ValueError('Stage4F private fault execution adapters changed')
    provenance = json.loads((root / 'source/provenance.json').read_text())
    sources = provenance.get('repositories', [])
    if {item.get('algorithm') for item in sources} != {
            'OpenRCA/RCA-agent', 'MicroIRC', 'MicroRCA'}:
        raise ValueError('Missing required pinned author source')
    for item in sources:
        if len(item.get('commit', '')) != 40 or len(item.get('sha256', '')) != 64 or (
                Path(item.get('archive', '')).name != item.get('archive')):
            raise ValueError('Malformed author source pin: ' + str(item.get('algorithm')))
    order = admission['execution_order']
    if not (order.index('freeze_all_contracts_and_code') < order.index('collect_new_fault_cases')
            < order.index('freeze_prediction_sha256') < order.index('open_evaluator_gt')):
        raise ValueError('Blind execution order is invalid')
    return data


def tracked_files(root):
    files = [root / 'design' / name for name in CONTRACTS]
    source = root / 'source/provenance.json'
    if not source.is_file():
        raise ValueError('Missing pinned author source provenance')
    files.append(source)
    for folder in ('scripts', 'algorithms'):
        files.extend(sorted((root / folder).glob('*.py')))
    if not any((root / 'algorithms').glob('*.py')):
        raise ValueError('No algorithm code available to freeze')
    return files


def verified_author_sources(root):
    provenance = json.loads((root / 'source/provenance.json').read_text())
    source_root = Path(provenance['source_archive_root'])
    verified = {}
    for item in provenance['repositories']:
        archive = source_root / item['archive']
        if not archive.is_file() or sha256(archive) != item['sha256']:
            raise ValueError('Pinned author source archive hash mismatch: ' + item['algorithm'])
        verified[item['algorithm']] = {'commit': item['commit'],
                                       'archive': item['archive'],
                                       'sha256': item['sha256']}
    return verified


def git_provenance(root):
    head = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', 'HEAD'],
                          capture_output=True, text=True, check=False)
    return {'head': head.stdout.strip() if head.returncode == 0 else None,
            'status': 'HEAD_PRESENT' if head.returncode == 0 else 'NO_GIT_HEAD'}


def freeze(root, readiness_decision):
    lint(root)
    manifest = root / 'freeze-manifest.json'
    if manifest.exists():
        raise ValueError('Stage4F contracts are already frozen')
    if (root / 'cases').exists() or (root / 'runs').exists() or (root / 'evaluator_gt').exists():
        raise ValueError('Fault or run directories exist before contract freeze')
    if readiness_decision is None:
        raise ValueError('Explicit executable readiness decision is required')
    decision_file = readiness_decision.resolve()
    try:
        decision_name = decision_file.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError('Readiness decision must belong to this Stage4F root') from exc
    if decision_name.parts[0] != 'readiness' or decision_file.suffix != '.json':
        raise ValueError('Readiness decision must be an archived JSON under readiness/')
    decision = json.loads(decision_file.read_text())
    if (decision.get('status') != 'READY_TO_FREEZE' or decision.get('blockers') or
            decision.get('faults_injected') != 0 or
            decision.get('contracts_frozen') is not False):
        raise ValueError('Executable readiness decision does not authorize freeze')
    trace_source = yaml.safe_load((root / 'design/observation-contract.yaml').read_text())[
        'trace_acquisition']['source']
    if (trace_source != 'JAEGER_OPENSEARCH_STORAGE' or
            decision.get('live_converter', {}).get('trace_source') != trace_source):
        raise ValueError('Readiness and observation contract trace sources disagree')
    route_proof = root / ROUTE_PROOF
    if (not route_proof.is_file() or
            decision.get('f04_proof_sha256') != sha256(route_proof)):
        raise ValueError('F04 pre-fault public route proof changed since readiness')
    author_sources = verified_author_sources(root)
    files = tracked_files(root)
    checksums = {str(path.relative_to(root)): sha256(path) for path in files}
    current_code = {name: digest for name, digest in checksums.items()
                    if name.split('/', 1)[0] in ('design', 'scripts', 'algorithms')}
    if (decision.get('code_sha256') != current_code or
            decision.get('source_provenance_sha256') != checksums['source/provenance.json']):
        raise ValueError('Executable readiness code or source hash is stale')
    payload = {'status': 'STAGE4F_CONTRACTS_FROZEN_BEFORE_FAULT',
               'frozen_at_utc': datetime.now(timezone.utc).isoformat(),
               'executable_readiness_decision': str(decision_name),
               'executable_readiness_sha256': sha256(decision_file),
               'trace_source': trace_source,
               'git_provenance': git_provenance(root),
               'author_sources': author_sources,
               'case_selection_policy_sha256': sha256(root / 'design/case-plan.yaml'),
               'f04_route_proof': str(ROUTE_PROOF),
               'f04_route_proof_sha256': sha256(route_proof),
               'sha256': checksums}
    with manifest.open('x') as output:
        json.dump(payload, output, indent=2)
        output.write('\n')
    for path in files:
        path.chmod(0o444)
    decision_file.chmod(0o444)
    route_proof.chmod(0o444)
    manifest.chmod(0o444)
    return payload


def verify(root):
    manifest = json.loads((root / 'freeze-manifest.json').read_text())
    if manifest.get('status') != 'STAGE4F_CONTRACTS_FROZEN_BEFORE_FAULT':
        raise ValueError('Unknown freeze status')
    if (manifest.get('trace_source') != 'JAEGER_OPENSEARCH_STORAGE' or
            manifest['trace_source'] != yaml.safe_load(
                (root / 'design/observation-contract.yaml').read_text())[
                    'trace_acquisition']['source']):
        raise ValueError('Frozen trace acquisition source changed')
    decision_name = manifest.get('executable_readiness_decision')
    if (not isinstance(decision_name, str) or
            not decision_name.startswith('readiness/') or
            sha256(root / decision_name) != manifest.get('executable_readiness_sha256') or
            manifest.get('f04_route_proof') != str(ROUTE_PROOF) or
            sha256(root / ROUTE_PROOF) != manifest.get('f04_route_proof_sha256') or
            sha256(root / 'design/case-plan.yaml') !=
            manifest.get('case_selection_policy_sha256')):
        raise ValueError('Frozen readiness or case selection provenance changed')
    if verified_author_sources(root) != manifest.get('author_sources'):
        raise ValueError('Pinned author source provenance changed')
    current = {str(path.relative_to(root)) for path in tracked_files(root)}
    if current != set(manifest['sha256']):
        raise ValueError('Frozen contract or code file set changed')
    for name, expected in manifest['sha256'].items():
        if sha256(root / name) != expected:
            raise ValueError('Frozen contract or code changed: ' + name)
    lint(root)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('lint', 'freeze', 'verify'))
    parser.add_argument('root', type=Path)
    parser.add_argument('--readiness-decision', type=Path)
    args = parser.parse_args()
    if args.operation == 'lint':
        result = {'status': 'STAGE4F_CONTRACTS_LINT_PASS', 'files': len(lint(args.root))}
    elif args.operation == 'freeze':
        result = freeze(args.root, args.readiness_decision)
    else:
        result = verify(args.root)
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
