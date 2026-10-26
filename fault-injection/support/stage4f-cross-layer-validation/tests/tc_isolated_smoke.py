#!/usr/bin/env python3
"""Test the frozen F04 tc command sequence only in a disposable netns."""

import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from host_fault import path_qdisc_commands


NAME = 'stage4f-tc-smoke'
INTERFACE = 'sf4veth0'


def call(*args):
    return subprocess.run(args, check=True, capture_output=True,
                          text=True, timeout=30).stdout


def main():
    if call('hostname').strip() != 'k8s-worker3':
        raise ValueError('The isolated tc smoke belongs only on Worker3')
    if NAME in call('ip', 'netns', 'list'):
        raise ValueError('Isolated network namespace already exists')
    call('ip', 'netns', 'add', NAME)
    try:
        call('ip', '-n', NAME, 'link', 'add', INTERFACE, 'type', 'veth',
             'peer', 'name', 'sf4veth1')
        call('ip', '-n', NAME, 'link', 'set', INTERFACE, 'up')
        call('ip', '-n', NAME, 'link', 'set', 'sf4veth1', 'up')
        for command in path_qdisc_commands(INTERFACE):
            call('ip', 'netns', 'exec', NAME, *command)
        qdiscs = json.loads(call('ip', 'netns', 'exec', NAME, 'tc', '-j',
                                  'qdisc', 'show', 'dev', INTERFACE))
        filters = json.loads(call('ip', 'netns', 'exec', NAME, 'tc', '-j',
                                  'filter', 'show', 'dev', INTERFACE, 'parent', '1:'))
        if not any(row.get('root') is True and row.get('kind') == 'prio'
                   for row in qdiscs) or not any(row.get('kind') == 'netem'
                                                  for row in qdiscs) or not filters:
            raise ValueError('Isolated qdisc/filter plan was not installed')
        result = {'status': 'ISOLATED_F04_TC_PLAN_PASS',
                  'qdiscs': [row['kind'] for row in qdiscs],
                  'filter_count': len(filters),
                  'production_interface_untouched': True}
    finally:
        call('ip', 'netns', 'del', NAME)
    if NAME in call('ip', 'netns', 'list'):
        raise ValueError('Isolated network namespace was not removed')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
