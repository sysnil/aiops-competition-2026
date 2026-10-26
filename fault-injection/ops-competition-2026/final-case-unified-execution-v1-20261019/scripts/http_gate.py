"""Master-local per-request HTTP fault gate; no reusable lease authorizes headers."""

import os
import time
from pathlib import Path
from urllib.parse import urlparse

from action_decision import decision_lock, read_bound
from entry_policy import validate_action


def process_identity(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().split()
    return int(fields[21])


def header_for_request(action, runtime, method, url, *, unit_state,
                       clock=time.time, pid_start=process_identity):
    validate_action(action)
    if action['worker'] != 'k8s-master' or action['kind'] != 'HTTP':
        raise ValueError('HTTP_ACTION_SCOPE')
    parsed = urlparse(url)
    if method != 'GET' or parsed.path != '/' or parsed.query:
        return {}
    with decision_lock(runtime) as root:
        state = read_bound(root / 'state.json')
        permit = read_bound(root / 'permit.json')
        if (root / 'REVOKED.json').exists():
            raise ValueError('HTTP_ACTION_REVOKED')
        for key in ('run_id', 'action_id', 'manifest_sha256'):
            if state.get(key) != action[key] or permit.get(key) != action[key]:
                raise ValueError('HTTP_ACTION_IDENTITY_' + key)
        now = clock()
        if (state.get('status') != 'REGISTERED' or
                permit.get('status') != 'PERMITTED' or
                not permit['issued_epoch'] <= now < permit['expires_epoch'] or
                not state['planned_start_epoch'] <= now < state['planned_end_epoch']):
            raise ValueError('HTTP_ACTION_NOT_ACTIVE')
        if (state.get('requester_pid') != os.getpid() or
                pid_start(state['requester_pid']) != state['requester_start_ticks'] or
                pid_start(state['controller_pid']) != state['controller_start_ticks']):
            raise ValueError('HTTP_REQUESTER_OR_CONTROLLER_IDENTITY')
        controller = unit_state(state['controller_unit'])
        guard = unit_state(state['guard_unit'])
        if (controller.get('ActiveState') != 'active' or
                controller.get('MainPID') != str(state['controller_pid']) or
                guard.get('ActiveState') != 'active' or
                int(guard.get('MainPID', '0')) <= 0):
            raise ValueError('HTTP_CONTROLLER_OR_GUARD_NOT_LIVE')
        if (root / 'REVOKED.json').exists():
            raise ValueError('HTTP_REVOKED_BEFORE_HEADER')
        return {'x-envoy-fault-delay-request': '250'}


def controlled_request_factory(action, runtime, original_request, *, unit_state,
                               clock=time.time, pid_start=process_identity):
    """A replacement for urllib.request.Request inside the qualified workload."""
    def request(url, data=None, headers=None, method=None, **kwargs):
        outbound = dict(headers or {})
        if 'x-envoy-fault-delay-request' in {key.lower() for key in outbound}:
            raise ValueError('HTTP_DELAY_HEADER_ALREADY_PRESENT')
        outbound.update(header_for_request(action, runtime, method, url,
                          unit_state=unit_state, clock=clock,
                          pid_start=pid_start))
        return original_request(url, data=data, headers=outbound,
                                method=method, **kwargs)
    return request


def cancel_other_actions(actions, failed_action, cancel):
    """Cancel only exact registered siblings in the same Attempt."""
    failed = validate_action(failed_action)
    results = []
    for sibling in actions:
        validate_action(sibling)
        if sibling['run_id'] != failed['run_id']:
            raise ValueError('CROSS_ATTEMPT_CANCELLATION_FORBIDDEN')
        if sibling['action_id'] != failed['action_id']:
            results.append(cancel(sibling))
    return results
