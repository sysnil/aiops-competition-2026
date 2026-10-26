"""Exact Attempt/Action systemd Timer lifecycle for the new final Cases."""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from action_decision import create_once, read_bound


ACTIVE = {'active', 'activating', 'reloading'}


def inspect(unit, run=subprocess.run):
    result = {}
    for suffix in ('.timer', '.service'):
        proc = run(['systemctl', 'show', unit + suffix,
                    '--property=ActiveState', '--property=LoadState',
                    '--no-pager'], capture_output=True, text=True, timeout=10)
        if proc.returncode:
            raise RuntimeError('TIMER_STATE_QUERY_FAILED')
        values = dict(row.split('=', 1) for row in proc.stdout.splitlines() if '=' in row)
        if not {'ActiveState', 'LoadState'} <= values.keys():
            raise RuntimeError('TIMER_STATE_INCOMPLETE')
        result[suffix] = values
    return result


def cancel(runtime, action, manifest_sha, *, run=subprocess.run):
    runtime = Path(runtime)
    runtime.mkdir(parents=True, mode=0o700, exist_ok=True)
    if runtime.is_symlink():
        raise ValueError('TIMER_RUNTIME_SYMLINK')
    identity = {key: action[key] for key in ('run_id', 'action_id')}
    identity['manifest_sha256'] = manifest_sha
    marker = runtime / 'REVOKED.json'
    if marker.exists():
        previous = read_bound(marker)
        if any(previous.get(key) != value for key, value in identity.items()):
            raise ValueError('TIMER_REVOKE_OTHER_ATTEMPT')
    else:
        try:
            create_once(marker, {**identity, 'status': 'REVOKED',
                                 'at_utc': datetime.now(timezone.utc).isoformat()})
        except FileExistsError:
            previous = read_bound(marker)
            if any(previous.get(key) != value for key, value in identity.items()):
                raise ValueError('TIMER_REVOKE_OTHER_ATTEMPT')
    for suffix in ('.timer', '.service'):
        run(['systemctl', 'stop', action['unit'] + suffix],
            capture_output=True, text=True, timeout=20)
    states = inspect(action['unit'], run)
    if any(item['ActiveState'] in ACTIVE for item in states.values()):
        raise RuntimeError('TIMER_CANCEL_UNVERIFIED')
    return {'status': 'BOUND_TIMER_CANCELLED', 'identity': identity, 'states': states}


def arm(runtime, action, manifest_sha, argv, owner, *, run=subprocess.run):
    runtime = Path(runtime)
    runtime.mkdir(parents=True, mode=0o700, exist_ok=True)
    if runtime.is_symlink():
        raise ValueError('TIMER_RUNTIME_SYMLINK')
    if (any(owner.get(key) != action[key] for key in ('run_id', 'action_id')) or
            owner.get('manifest_sha256') != manifest_sha or
            owner.get('role') != 'MASTER_CONTROLLER_ONLY'):
        raise ValueError('TIMER_OWNER_IDENTITY')
    if ((runtime / 'REVOKED.json').exists() or
            (runtime / 'started.json').exists() or
            (runtime / 'arm-intent.json').exists() or
            any(item['ActiveState'] in ACTIVE for item in
                inspect(action['unit'], run).values())):
        raise ValueError('TIMER_REARM_OR_DUPLICATE')
    try:
        create_once(runtime / 'arm-intent.json', {
            'run_id': action['run_id'], 'action_id': action['action_id'],
            'manifest_sha256': manifest_sha, 'unit': action['unit']})
        calendar = datetime.fromisoformat(action['planned_start_utc']).astimezone(
            timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f UTC')
        proc = run(['systemd-run', '--unit=' + action['unit'],
                    '--on-calendar=' + calendar,
                    '--timer-property=AccuracySec=10ms',
                    '--timer-property=RandomizedDelaySec=0',
                    '--timer-property=Persistent=false',
                    '--property=RuntimeMaxSec=340', *argv],
                   capture_output=True, text=True, timeout=15)
        if proc.returncode or inspect(action['unit'], run)['.timer']['ActiveState'] not in ACTIVE:
            raise RuntimeError('TIMER_ARM_FAILED_OR_INACTIVE')
        create_once(runtime / 'armed.json', {'calendar': calendar,
            'run_id': action['run_id'], 'action_id': action['action_id'],
            'manifest_sha256': manifest_sha})
        return {'status': 'BOUND_TIMER_ARMED', 'calendar': calendar}
    except BaseException:
        cancel(runtime, action, manifest_sha, run=run)
        raise
