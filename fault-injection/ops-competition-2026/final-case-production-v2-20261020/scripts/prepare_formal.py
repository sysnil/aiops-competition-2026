#!/usr/bin/env python3
"""Prepare one immutable final-case Manifest after real mechanism qualification."""

import argparse
import hashlib
import json
import os
import shlex
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
ROOT = OPS / 'final-case-production-v2-20261020'
FORMAL = OPS / 'final-case-formal-entry-v7-20261020'
SHARED = OPS / 'final-case-unified-execution-v2-20261019/scripts'
AUTH_SHARED = OPS / 'final-case-unified-execution-v1-20261019/scripts'
QUAL = OPS / 'final-case-mechanism-qualification-v3-20261020/scripts'
HTTP_QUAL = OPS / 'final-case-http-qualification-v1-20261020/scripts'
ADAPTER = OPS / 'sf013-adapter-v5-20261019/scripts/qualified_adapter.py'
DATA = Path('/data/stage5-v3/ops-competition-2026')
SOCKETS = {'k8s-worker2': ('/run/ops-final-ssh/w2.sock', '192.168.53.54'),
           'k8s-worker3': ('/run/ops-final-ssh/w3.sock', '192.168.53.55')}
WATCHDOGS = {
    'k8s-worker2': OPS / 'qualification/SF-FINAL-WATCHDOG-W2-V5-20261019.json',
    'k8s-worker3': OPS / 'qualification/SF-FINAL-WATCHDOG-W3-V5-20261019.json'}
SEEDS = {'SF-013': 41013, 'SF-014': 41014, 'SF-015': 41015,
         'SF-016': 41016, 'SF-017': 41017}

sys.path.insert(0, str(FORMAL / 'scripts'))
sys.path.insert(0, str(QUAL))
sys.path.insert(0, str(HTTP_QUAL))
from control_contract import load_manifest, sha
from safety_monitor import sample as safety_sample
from entry_policy import CASE_ACTIONS
from issue_receipt import issue
from issue_http_receipt import issue as issue_http
from run_qualification import inspect_targets, registration


def utc(epoch=None):
    return datetime.fromtimestamp(time.time() if epoch is None else epoch,
                                  timezone.utc).isoformat()


def save(path, value):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
    path.chmod(0o600)


def remote(worker, argv):
    channel, host = SOCKETS[worker]
    result = subprocess.run(['ssh', '-S', channel, '-o', 'BatchMode=yes',
        '-o', 'ConnectTimeout=3', 'root@' + host, shlex.join(map(str, argv))],
        capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError('FORMAL_PREPARE_WORKER_UNAVAILABLE:' + worker)
    return result.stdout


def copy_worker(worker, path):
    channel, host = SOCKETS[worker]
    remote(worker, ['mkdir', '-p', '-m', '700', path.parent])
    subprocess.run(['scp', '-q', '-o', 'BatchMode=yes',
        '-o', 'ControlPath=' + channel, str(path),
        'root@' + host + ':' + str(path)], check=True,
        capture_output=True, timeout=20)
    if remote(worker, ['sha256sum', path]).split()[0] != sha(path):
        raise RuntimeError('FORMAL_PREPARE_WORKER_HASH_MISMATCH')


def ensure_worker_sources(worker, sources):
    for source, digest in sources.items():
        path = Path(source)
        probe = remote(worker, ['python3', '-c',
            'import pathlib; p=pathlib.Path(' + repr(source) +
            '); print("LINK" if p.is_symlink() else "FILE" if p.is_file() '
            'else "MISSING")']).strip()
        if probe == 'MISSING':
            copy_worker(worker, path)
        elif probe != 'FILE':
            raise RuntimeError('FORMAL_WORKER_SOURCE_SYMLINK:' + source)
        if remote(worker, ['sha256sum', path]).split()[0] != digest:
            raise RuntimeError('FORMAL_WORKER_SOURCE_VERSION_MISMATCH:' + source)


def source_lock():
    paths = [*sorted((FORMAL / 'scripts').glob('*.py')),
             *sorted(SHARED.glob('*.py')),
             *[AUTH_SHARED / name for name in
               ('master_authorizer.py', 'auth_protocol.py', 'action_decision.py')],
             *sorted((ROOT / 'scripts').glob('*.py')),
             *sorted((ROOT / 'design').glob('*.json')),
             *sorted((OPS / 'low-stable-v4-20261017/capture').glob('*.py')),
             OPS / 'final-case-observation-v2-20261020/scripts/assess_collection.py',
             OPS / 'final-case-observation-v2-20261020/design/collection-contract-draft.json',
             OPS / 'final-case-fail-closed-v1-20261019/scripts/calico_bgp_audit.py',
             QUAL / 'issue_receipt.py',
             QUAL / 'assess_qualification.py',
             QUAL.parent / 'design/control-sample-contract.json',
             HTTP_QUAL / 'http_qual.py',
             HTTP_QUAL / 'assess_http_qual.py',
             HTTP_QUAL / 'issue_http_receipt.py',
             OPS / 'sf013-formal-v4-20261019/design/workload-policy.json',
             OPS / 'low-stable-v4-20261017/formal-v2/fixed_business_streams_plain.py',
             ADAPTER,
             Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py'),
             Path('/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml'),
             OPS / 'low-stable-v4-20261017/design/load-profiles-v4b.yaml',
             Path('/root/cross-layer-workload-generation/design/people.json')]
    unique = sorted(set(paths))
    if any(not path.is_file() or path.is_symlink() for path in unique):
        raise RuntimeError('FORMAL_SOURCE_MISSING_OR_SYMLINK')
    return {str(path): sha(path) for path in unique}


def controller_source_lock(sources):
    roots = (FORMAL, SHARED, AUTH_SHARED)
    external = {
        str(ADAPTER),
        '/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py',
        '/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml',
        str(OPS / 'low-stable-v4-20261017/design/load-profiles-v4b.yaml'),
        '/root/cross-layer-workload-generation/design/people.json',
    }
    return {path: digest for path, digest in sources.items()
            if path in external or any(Path(path).resolve().is_relative_to(
                root.resolve()) for root in roots)}


def fresh_environment(run_id, actions, private):
    basic = safety_sample({'run_id': run_id, 'actions': actions})
    if basic['status'] != 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS':
        raise RuntimeError('FORMAL_FIVE_HOST_SAFETY_BLOCKED')
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    bgp = private / 'calico-bgp.json'
    subprocess.run(['/usr/bin/python3', '-B',
        str(OPS / 'final-case-fail-closed-v1-20261019/scripts/calico_bgp_audit.py'),
        '--output', str(bgp)], check=True, capture_output=True, timeout=90)
    pods = json.loads(subprocess.check_output(['kubectl', 'get', 'pods', '-A',
        '-o', 'json'], text=True, timeout=30))['items']
    required = {
        'otel-demo': ('frontend-', 'recommendation-', 'otel-collector-',
                      'prometheus-', 'opensearch-', 'jaeger-'),
        'kube-system': ('coredns-',),
        'calico-system': ('calico-node-',)}
    found = {}
    for namespace, names in required.items():
        for prefix in names:
            matches = [pod for pod in pods if
                       pod['metadata']['namespace'] == namespace and
                       pod['metadata']['name'].startswith(prefix)]
            if not matches or any(pod['status']['phase'] != 'Running' or
                    not all(item.get('ready') for item in
                            pod['status'].get('containerStatuses', []))
                    for pod in matches):
                raise RuntimeError('FORMAL_REQUIRED_POD_NOT_READY:' + prefix)
            found[prefix] = len(matches)
    clocks = {}
    for worker in sorted({a['worker'] for a in actions
                          if a['worker'] != 'k8s-master'}):
        before = time.time()
        reading = float(remote(worker, ['date', '-u', '+%s.%N']).strip())
        after = time.time()
        offset = reading - (before + after) / 2
        uncertainty = (after - before) / 2
        if abs(offset) + uncertainty > .5:
            raise RuntimeError('FORMAL_WORKER_CLOCK_UNCERTAIN:' + worker)
        clocks[worker] = {'offset_seconds': offset,
                          'uncertainty_seconds': uncertainty}
    return {'status': 'FRESH_GLOBAL_SAFETY_PASS', 'run_id': run_id,
            'at_utc': utc(), 'basic': basic,
            'calico_bgp_sha256': sha(bgp),
            'required_pod_counts': found,
            'worker_clock': clocks}


def prepare(case, attempt, qualifications):
    if socket.gethostname() != 'k8s-master' or case not in SEEDS or \
            not 1 <= attempt <= 99:
        raise ValueError('FORMAL_PREPARE_CASE_OR_MASTER')
    run = f'{case}-ATTEMPT-{attempt:02d}'
    private = FORMAL / 'runs' / run
    if private.exists() or (DATA / 'runs' / run).exists():
        raise ValueError('FORMAL_ATTEMPT_ALREADY_EXISTS')
    if len(qualifications) != len(CASE_ACTIONS[case]):
        raise ValueError('FORMAL_QUALIFICATION_COUNT')
    for kind, path in qualifications.items():
        decision = read(path)
        if (kind not in CASE_ACTIONS[case] or
                decision.get('case') != case or
                decision.get('status') != 'QUALIFIED_FOR_THIS_VERSION' or
                decision.get('blockers')):
            raise ValueError('FORMAL_MECHANISM_NOT_QUALIFIED:' + kind)
    private.mkdir(parents=True, mode=0o700)
    t0 = time.time() + 150
    actions = []
    for index, (kind, (worker, mechanism, duration)) in enumerate(
            CASE_ACTIONS[case].items()):
        action_id = run + '-' + kind
        action = {'run_id': run, 'action_id': action_id, 'kind': kind,
                  'worker': worker, 'mechanism': mechanism,
                  'duration_seconds': duration,
                  'capability': 'BOUND_QUALIFIED_FAULT',
                  'entrypoint': str(SHARED / (
                      'http_gate.py' if kind == 'HTTP' else 'worker_entry.py')),
                  'timer_command': str(FORMAL / 'scripts' / (
                      'http_workload.py' if kind == 'HTTP' else
                      'worker_fault_entry.py')),
                  'planned_start_utc': utc(t0 + (960 if case == 'SF-017' and
                                                kind == 'SERVICE' else 600)),
                  'runtime': str(FORMAL / 'runtime' / run / action_id)}
        if kind == 'HTTP':
            action['min_fault_requests'] = 60 if duration == 300 else 48
            action_path = private / (action_id + '-action.json')
            save(action_path, action)
            decision = qualifications[kind]
            qual_plan = (HTTP_QUAL.parent / 'runs' /
                         read(decision)['qualification_id'] / 'plan.json')
            receipt = issue_http(decision, qual_plan, action_path)
            receipt_path = private / (action_id + '-receipt.json')
            save(receipt_path, receipt)
            action['qualification'] = {
                'status': 'QUALIFIED_FOR_THIS_VERSION',
                'receipt_path': str(receipt_path),
                'receipt_sha256': sha(receipt_path),
                'adapter_sha256': sha(FORMAL / 'scripts/http_workload.py')}
            actions.append(action)
            continue
        action['unit'] = 'ops-' + case.lower().replace('-', '') + '-' + action_id.lower()
        targets = inspect_targets('frontend' if kind == 'POD' else
                                  'recommendation')
        reg = registration(action_id, case, targets)
        reg['formal_case'] = True
        reg_path = private / (action_id + '-registration.json')
        save(reg_path, reg)
        action.update(qualified_registration=str(reg_path),
                      qualified_registration_sha256=sha(reg_path),
                      injection_output=str(OPS / 'qualification/private-attempts' /
                                           action_id))
        action_path = private / (action_id + '-action.json')
        save(action_path, action)
        decision = qualifications[kind]
        qualification_reg = (QUAL.parent / 'runs' /
                             read(decision)['qualification_id'] /
                             'registration.json')
        receipt = issue(decision, qualification_reg, action_path)
        receipt_path = private / (action_id + '-receipt.json')
        save(receipt_path, receipt)
        action['qualification'] = {
            'status': 'QUALIFIED_FOR_THIS_VERSION',
            'receipt_path': str(receipt_path),
            'receipt_sha256': sha(receipt_path),
            'adapter_sha256': sha(ADAPTER),
            'watchdog_proof_file': str(WATCHDOGS[worker]),
            'watchdog_proof_sha256': sha(WATCHDOGS[worker])}
        actions.append(action)
    source = source_lock()
    controller_sources = controller_source_lock(source)
    for worker in sorted({action['worker'] for action in actions
                          if action['worker'] != 'k8s-master'}):
        ensure_worker_sources(worker, controller_sources)
    approval = private / 'human-approval.json'
    save(approval, {'status': 'HUMAN_PRODUCTION_APPROVED',
        'run_id': run, 'scope': 'One bounded final Case using registered actions only',
        'basis': 'Current user instruction to start generating SF-013..SF-017',
        'not_a_gt_approval': True, 'at_utc': utc()})
    safety = fresh_environment(run, actions, private)
    safety_path = private / 'safety-snapshot.json'
    save(safety_path, safety)
    storage_path = private / 'storage-snapshot.json'
    save(storage_path, {'status': 'FIVE_HOST_STORAGE_MEASURED',
        'run_id': run, 'at_utc': utc(),
        'filesystems': safety['basic']['space']})
    controller = 'ops-' + case.lower().replace('-', '') + '-' + run.lower() + '-controller'
    manifest = {'schema': 'FINAL_CASE_UNIFIED_ENTRY_V1',
        'mode': 'FORMAL_CANDIDATE', 'run_id': run,
        'start_utc': utc(t0), 'window_end_utc': utc(t0 + 1800),
        'workload_seed': SEEDS[case], 'safety': 'FRESH_GLOBAL_SAFETY_PASS',
        'actions': actions,
        'controller_unit': controller,
        'guard_unit': controller + '-guard',
        'monitor_unit': controller + '-monitor',
        'source_sha256': controller_sources,
        'human_approval': {'path': str(approval), 'sha256': sha(approval)},
        'safety_snapshot': {'path': str(safety_path),
                            'sha256': sha(safety_path)},
        'storage_snapshot': {'path': str(storage_path),
                             'sha256': sha(storage_path)}}
    manifest_path = private / 'manifest.json'
    save(manifest_path, manifest)
    digest = sha(manifest_path)
    load_manifest(manifest_path, digest, allow_rehearsal=False)
    for action in actions:
        if action['worker'] != 'k8s-master':
            copy_worker(action['worker'], Path(action['qualified_registration']))
            copy_worker(action['worker'], Path(action['qualification']['receipt_path']))
    freeze = private / 'freeze-manifest.json'
    save(freeze, {'status': 'FROZEN_FORMAL_CANDIDATE',
        'run_id': run, 'manifest_path': str(manifest_path),
        'manifest_sha256': digest, 'trace_source': 'JAEGER_OPENSEARCH_STORAGE',
        'source_sha256': source, 'at_utc': utc(),
        'qualification_receipt_sha256': {
            action['action_id']: action['qualification']['receipt_sha256']
            for action in actions}})
    return {'status': 'FORMAL_ATTEMPT_PREPARED_NOT_STARTED',
            'run_id': run, 'manifest_path': str(manifest_path),
            'manifest_sha256': digest, 'freeze_path': str(freeze),
            'freeze_sha256': sha(freeze), 'start_utc': manifest['start_utc']}


def read(path):
    return json.loads(Path(path).read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=tuple(SEEDS), required=True)
    parser.add_argument('--attempt', type=int, required=True)
    parser.add_argument('--worker-decision', type=Path)
    parser.add_argument('--http-decision', type=Path)
    args = parser.parse_args()
    qualifications = {}
    if args.worker_decision is not None:
        qualifications['POD' if args.case in ('SF-013', 'SF-016') else
                       'SERVICE'] = args.worker_decision
    if args.http_decision is not None:
        qualifications['HTTP'] = args.http_decision
    print(json.dumps(prepare(args.case, args.attempt, qualifications),
                     sort_keys=True))


if __name__ == '__main__':
    main()
