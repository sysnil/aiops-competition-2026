#!/usr/bin/env python3
"""Independent exact-Attempt Marker Guard."""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from guard_core import watch
from marker_controller import ROOT, remote


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--registration', type=Path, required=True)
    parser.add_argument('--registration-sha', required=True)
    args = parser.parse_args()
    if sha(args.registration) != args.registration_sha:
        raise RuntimeError('MARKER_GUARD_REGISTRATION_SHA')
    reg = json.loads(args.registration.read_text())
    reg['manifest_sha256'] = args.registration_sha
    root = args.registration.parent
    try:
        result = watch(reg['controller_unit'],
                       root / 'controller-started.json',
                       root / 'controller-terminal.json',
                       datetime.fromisoformat(reg['planned_utc']).timestamp() + 6,
                       lambda: remote(reg, 'cancel'))
        result['run_id'] = reg['run_id']
        result['at_utc'] = datetime.now(timezone.utc).isoformat()
        if result['worker_cancel']['status'] != 'ATTEMPT_TIMER_REVOKED_AND_INACTIVE':
            raise RuntimeError('MARKER_GUARD_CANCEL_UNVERIFIED')
        with (root / 'guard-terminal.json').open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        print(json.dumps(result), flush=True)
    except BaseException as exc:
        failure = {'status': 'MARKER_GUARD_CLEANUP_UNVERIFIED',
                   'error': type(exc).__name__ + ': ' + str(exc),
                   'at_utc': datetime.now(timezone.utc).isoformat()}
        if not (root / 'guard-failure.json').exists():
            with (root / 'guard-failure.json').open('x') as stream:
                json.dump(failure, stream, indent=2)
                stream.write('\n')
        raise


if __name__ == '__main__':
    main()
