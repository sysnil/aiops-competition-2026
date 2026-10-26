#!/usr/bin/env python3
"""Stop only orphaned processes bound to one independent attempt directory."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import time


ROOT = Path('/root/stage5-v3-autonomous-preparation')


def process_cmdline(pid):
    try:
        raw = (Path('/proc') / str(pid) / 'cmdline').read_bytes()
        return [part.decode(errors='replace') for part in raw.split(b'\0') if part]
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return []


def matching(attempt):
    markers = (str(ROOT / 'attempts' / attempt), str(ROOT / 'runs' / attempt))
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        pid = int(entry.name)
        args = process_cmdline(pid)
        if not args or 'python' not in Path(args[0]).name:
            continue
        if any(marker in ' '.join(args) for marker in markers) or ('--run-id' in args and
                                        args[args.index('--run-id') + 1] == attempt):
            found.append({'pid': pid, 'argv': args})
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--id', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'S5V3AP-[A-Z0-9-]+', args.id) or args.output.exists() or (
            not any(args.output.is_relative_to(ROOT / kind / args.id)
                    for kind in ('attempts', 'runs'))):
        raise ValueError('Unregistered-looking attempt or unsafe output')
    before = matching(args.id)
    for row in before:
        try:
            os.kill(row['pid'], signal.SIGTERM)
        except ProcessLookupError:
            pass
    time.sleep(2)
    after = matching(args.id)
    for row in after:
        try:
            os.kill(row['pid'], signal.SIGKILL)
        except ProcessLookupError:
            pass
    time.sleep(.2)
    remaining = matching(args.id)
    result = {'attempt_id': args.id, 'at_utc': datetime.now(timezone.utc).isoformat(),
              'terminated': before, 'forced': after, 'remaining': remaining,
              'status': 'ATTEMPT_PROCESSES_STOPPED' if not remaining else 'STOP_INCOMPLETE'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'terminated_count': len(before),
                      'remaining_count': len(remaining)}))
    if remaining:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
