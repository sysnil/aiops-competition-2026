#!/usr/bin/env python3
"""Use the existing guarded Collector patch with this study's independent timer."""

import argparse
import importlib.util
import json
from pathlib import Path


OLD = Path('/root/stage5-v3-collector-reliability/scripts/collector_change.py')
TIMER = 's5v3ffr-collector-rollback.timer'


def old_module():
    spec = importlib.util.spec_from_file_location('collector_change', OLD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TIMER = TIMER
    module.ROOT = Path('/root/stage5-v3-final-fast-ready')
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('backup', 'plan', 'apply', 'rollback'))
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--prereg', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--health', type=Path)
    parser.add_argument('--residue', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    original = old_module()
    if args.action == 'backup':
        result = original.backup(args.backup_dir)
    elif args.action == 'plan':
        result = original.plan(args.backup_dir, args.prereg, args.output)
    elif args.action == 'apply':
        result = original.apply(args.plan, args.health, args.residue, args.output)
    else:
        result = original.rollback(original.read(args.plan), args.output)
    print(json.dumps(result), flush=True)


if __name__ == '__main__': main()
