#!/usr/bin/env python3
"""Use the verified ordinary state machine without any fault-header gate."""

import argparse
import json
import sys
from pathlib import Path


formal = Path('/root/ops-competition-2026/final-case-formal-entry-v7-20261020/scripts')
if not formal.is_dir():
    formal = Path(__file__).resolve().parents[2] / 'final-case-formal-entry-v7-20261020/scripts'
sys.path.insert(0, str(formal))
from http_workload import (BASE, MACHINE, PEOPLE, PROFILES,
                           load_real_business_module)


def run(start_utc, seed, output):
    if output.exists():
        raise ValueError('NORMAL_WORKLOAD_OUTPUT_ALREADY_EXISTS')
    module = load_real_business_module()
    args = argparse.Namespace(machine=str(MACHINE), profiles=str(PROFILES),
        people=str(PEOPLE), profile='LOW', seed=seed,
        start_utc=start_utc, duration=1800, base_url=BASE,
        allow_errors=True, output=str(output))
    result = module.run(args)
    if result['status'] not in ('LOAD_PASS', 'LOAD_COMPLETED_WITH_ERRORS'):
        raise RuntimeError('NORMAL_WORKLOAD_INCOMPLETE')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-utc', required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.start_utc, args.seed, args.output),
                     sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
