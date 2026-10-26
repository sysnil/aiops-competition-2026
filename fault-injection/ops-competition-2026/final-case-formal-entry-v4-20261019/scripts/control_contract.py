"""Single DRAFT contract for SF-013..017; never loads a legacy lease entry."""

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '/root/ops-competition-2026/final-case-unified-execution-v2-20261019/scripts')
from entry_policy import CASE_ACTIONS, validate_action


ROOT = Path('/root/ops-competition-2026/final-case-formal-entry-v4-20261019')
SHARED = Path('/root/ops-competition-2026/final-case-unified-execution-v2-20261019/scripts')
AUTH_SHARED = Path('/root/ops-competition-2026/final-case-unified-execution-v1-20261019/scripts')
HEX64 = re.compile(r'^[0-9a-f]{64}$')
RUN = re.compile(r'^(SF-01[3-7])-ATTEMPT-([0-9]{2}|MARKERTEST-[A-Z0-9]{8})$')
WORKER_ENTRY = ROOT / 'scripts/worker_fault_entry.py'
MASTER_HTTP = ROOT / 'scripts/http_workload.py'
WORKER_ADAPTER = Path('/root/ops-competition-2026/sf013-adapter-v4-20261019/scripts/qualified_adapter.py')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_file(path, expected_sha):
    path = Path(path)
    if (path.is_symlink() or not path.is_file() or
            not HEX64.fullmatch(expected_sha) or sha(path) != expected_sha):
        raise ValueError('FORMAL_MANIFEST_FILE_HASH')
    return json.loads(path.read_text())


def qualification_scope(action):
    fields = {key: action[key] for key in (
        'run_id', 'action_id', 'kind', 'worker', 'mechanism',
        'duration_seconds')}
    if action['worker'] != 'k8s-master':
        fields['qualified_registration_sha256'] = action[
            'qualified_registration_sha256']
    else:
        fields['min_fault_requests'] = action['min_fault_requests']
    payload = json.dumps(fields, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(payload).hexdigest()


def load_manifest(path, expected_sha, *, allow_rehearsal=True):
    value = read_file(path, expected_sha)
    match = RUN.fullmatch(value.get('run_id', ''))
    if match is None:
        raise ValueError('FORMAL_RUN_ID_SCOPE')
    case, suffix = match.groups()
    rehearsal = suffix.startswith('MARKERTEST-')
    if rehearsal and not allow_rehearsal:
        raise ValueError('REHEARSAL_NOT_FORMAL')
    if value.get('schema') != 'FINAL_CASE_UNIFIED_ENTRY_V1':
        raise ValueError('FORMAL_SCHEMA')
    if value.get('mode') != ('HARMLESS_REHEARSAL' if rehearsal else 'FORMAL_CANDIDATE'):
        raise ValueError('FORMAL_MODE')
    actions = value.get('actions')
    controller = value.get('controller_unit', '')
    if (controller != 'ops-' + case.lower().replace('-', '') + '-' +
            value['run_id'].lower() + '-controller' or
            value.get('guard_unit') != controller + '-guard' or
            value.get('monitor_unit') != controller + '-monitor' or
            len(controller) > 180):
        raise ValueError('FORMAL_CONTROLLER_GUARD_UNIT_IDENTITY')
    if (not isinstance(actions, list) or
            len(actions) != len(CASE_ACTIONS[case]) or
            len({a.get('action_id') for a in actions}) != len(actions) or
            {a.get('kind') for a in actions} != set(CASE_ACTIONS[case])):
        raise ValueError('FORMAL_ACTION_SET')
    for action in actions:
        if action.get('run_id') != value['run_id'] or 'manifest_sha256' in action:
            raise ValueError('FORMAL_ACTION_MANIFEST_BINDING')
        action['manifest_sha256'] = expected_sha
        validate_action(action, marker=rehearsal)
        expected = str(WORKER_ENTRY if action['worker'] != 'k8s-master' else MASTER_HTTP)
        if action.get('timer_command') != expected:
            raise ValueError('FORMAL_TIMER_OR_HTTP_COMMAND')
        if action['worker'] != 'k8s-master':
            unit = action.get('unit', '')
            if (unit != 'ops-' + case.lower().replace('-', '') + '-' +
                    action['action_id'].lower() or len(unit) > 180):
                raise ValueError('FORMAL_WORKER_UNIT_IDENTITY')
        if not rehearsal:
            if action['worker'] != 'k8s-master':
                for key in ('qualified_registration',
                            'qualified_registration_sha256', 'injection_output'):
                    if not action.get(key):
                        raise ValueError('FORMAL_WORKER_REGISTRATION_MISSING')
                if (not HEX64.fullmatch(action['qualified_registration_sha256']) or
                        not Path(action['qualified_registration']).is_file() or
                        sha(action['qualified_registration']) !=
                        action['qualified_registration_sha256']):
                    raise ValueError('FORMAL_WORKER_REGISTRATION_HASH')
        if action['kind'] == 'HTTP' and not rehearsal:
            if (not isinstance(action.get('min_fault_requests'), int) or
                    action['min_fault_requests'] < 1):
                raise ValueError('HTTP_MINIMUM_REQUESTS_NOT_REGISTERED')
        if not rehearsal:
            qualification = action.get('qualification')
            if (not isinstance(qualification, dict) or
                    qualification.get('status') != 'QUALIFIED_FOR_THIS_VERSION' or
                    not HEX64.fullmatch(qualification.get('receipt_sha256', '')) or
                    not qualification.get('receipt_path')):
                raise ValueError('FORMAL_ADAPTER_NOT_QUALIFIED')
            receipt = read_file(qualification['receipt_path'],
                                qualification['receipt_sha256'])
            expected_adapter = (WORKER_ADAPTER if action['worker'] !=
                                'k8s-master' else MASTER_HTTP)
            if (receipt.get('status') != 'QUALIFIED_FOR_THIS_VERSION' or
                    receipt.get('run_id') != action['run_id'] or
                    receipt.get('action_id') != action['action_id'] or
                    receipt.get('scope_sha256') != qualification_scope(action) or
                    receipt.get('adapter_sha256') != sha(expected_adapter)):
                raise ValueError('FORMAL_ADAPTER_NOT_QUALIFIED')
    start = datetime.fromisoformat(value['start_utc']).timestamp()
    rehearsal_duration = value.get('rehearsal_duration_seconds', 600)
    if rehearsal and rehearsal_duration not in (600, 1800):
        raise ValueError('REHEARSAL_DURATION_SCOPE')
    end = start + (rehearsal_duration if rehearsal else 1800)
    if rehearsal_duration == 1800 and rehearsal:
        expected_root = (Path('/data/stage5-v3/ops-competition-2026/rehearsals') /
                         (value['run_id'] + '-full1800'))
        if value.get('rehearsal_data_root') != str(expected_root):
            raise ValueError('FULL_REHEARSAL_DATA_BOUNDARY')
    if any(not start <= datetime.fromisoformat(a['planned_start_utc']).timestamp()
           <= end - a['duration_seconds'] for a in actions):
        raise ValueError('ACTION_OUTSIDE_CAPTURE_WINDOW')
    if ((not rehearsal or rehearsal_duration == 1800) and
            datetime.fromisoformat(value.get('window_end_utc', '')).timestamp() != end):
        raise ValueError('FORMAL_WINDOW_END')
    if case == 'SF-017':
        by_kind = {a['kind']: a for a in actions}
        http_start = datetime.fromisoformat(
            by_kind['HTTP']['planned_start_utc']).timestamp()
        service_start = datetime.fromisoformat(
            by_kind['SERVICE']['planned_start_utc']).timestamp()
        if (by_kind['HTTP']['action_id'] == by_kind['SERVICE']['action_id'] or
                abs(http_start - service_start) <
                by_kind['HTTP']['duration_seconds'] + 120):
            raise ValueError('SF017_ACTIONS_NOT_INDEPENDENT')
    source = value.get('source_sha256')
    if not isinstance(source, dict):
        raise ValueError('FORMAL_SOURCE_MANIFEST_MISSING')
    required = {str(ROOT / 'scripts' / name) for name in (
        'control_contract.py', 'worker_fault_entry.py', 'timer_lifecycle.py',
        'qualified_effect.py', 'http_workload.py', 'formal_controller.py',
        'formal_guard.py', 'start_formal.py', 'safety_monitor.py')}
    required.update(str(SHARED / name) for name in (
        'action_decision.py', 'auth_protocol.py', 'entry_policy.py',
        'http_gate.py', 'worker_auth.py',
        'worker_entry.py'))
    required.update(str(AUTH_SHARED / name) for name in (
        'master_authorizer.py', 'auth_protocol.py', 'action_decision.py'))
    if not rehearsal and any(a['kind'] == 'HTTP' for a in actions):
        required.update((
            '/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py',
            '/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml',
            '/root/ops-competition-2026/low-stable-v4-20261017/design/load-profiles-v4b.yaml',
            '/root/cross-layer-workload-generation/design/people.json'))
    if not required <= set(source):
        raise ValueError('FORMAL_REQUIRED_SOURCE_HASH_MISSING')
    for file_path, expected in source.items():
        file_path = Path(file_path)
        if (file_path.is_symlink() or not file_path.is_file() or
                not (any(file_path.resolve().is_relative_to(root.resolve())
                         for root in (ROOT, SHARED, AUTH_SHARED)) or
                     str(file_path) in required) or
                sha(file_path) != expected):
            raise ValueError('FORMAL_SOURCE_CHANGED')
    if not rehearsal:
        for field in ('human_approval', 'safety_snapshot', 'storage_snapshot'):
            item = value.get(field)
            if (not isinstance(item, dict) or
                    not HEX64.fullmatch(item.get('sha256', '')) or
                    not item.get('path')):
                raise ValueError('FORMAL_' + field.upper() + '_REQUIRED')
    return value


def command_for_worker(manifest_path, digest, action):
    return ['/usr/bin/python3', '-B', str(WORKER_ENTRY), 'fire',
            '--manifest', str(manifest_path), '--manifest-sha', digest,
            '--action-id', action['action_id']]
