#!/usr/bin/env python3
"""Verify independent workload evidence and write a SHA-256 archive index."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


EXPECTED_STAGE5_V2_FREEZE='6ebafa764b904950c00904d06cebae68c66727a8cf703158aa259bb728cd6452'


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--stage5-v2',type=Path,required=True)
    parser.add_argument('--final-health',type=Path,required=True)
    parser.add_argument('--final-residue',type=Path,required=True)
    args=parser.parse_args()
    root=args.root
    checks={}
    frozen_sha=sha(args.stage5_v2/'freeze-manifest.json')
    checks['stage5_v2_freeze_unchanged']=frozen_sha==EXPECTED_STAGE5_V2_FREEZE
    checks['final_health']=json.loads(args.final_health.read_text())['status']=='STAGE5_BASE_HEALTH_PASS'
    checks['final_five_host_residue']=json.loads(args.final_residue.read_text())['status']=='HOST_RESIDUE_PASS'
    runs={}
    for run in sorted((root/'runs').iterdir()):
        manifest=run/'result-manifest.json'
        if not manifest.exists(): continue
        data=json.loads(manifest.read_text())
        runs[run.name]={'status':data['status'],
            'sessions_hash_matches':sha(run/'sessions.jsonl')==data['sessions_sha256'],
            'planned_equals_recorded':data['planned_sessions']==data['completed_sessions']}
    checks['no_fault_runs_hashed']=all(row['sessions_hash_matches'] and
        row['planned_equals_recorded'] for row in runs.values())
    attempts={}
    for attempt in sorted((root/'attempts').iterdir()):
        result=attempt/'result.json'
        if result.exists():
            data=json.loads(result.read_text())
            attempts[attempt.name]={'status':data['status'],
                                    'fault_results_cleaned':all(
                                        json.loads(path.read_text()).get('status')=='CLEANED'
                                        for path in attempt.glob('state*.json'))}
        elif (attempt/'failure-record.json').exists():
            attempts[attempt.name]={'status':'INCOMPLETE_REHEARSAL',
                                    'preserved':True}
    checks['completed_attempts_cleaned']=all(row.get('fault_results_cleaned',True)
        for row in attempts.values() if row['status']=='COMPLETE')
    index={}
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.name not in {'sha256-manifest.json','archive-integrity.json'}:
            index[str(path.relative_to(root))]={'sha256':sha(path),'bytes':path.stat().st_size}
    result={'created_at_utc':datetime.now(timezone.utc).isoformat(),
            'stage5_v2_freeze_sha256':frozen_sha,'checks':checks,
            'runs':runs,'attempts':attempts,'indexed_files':len(index)}
    (root/'archive-integrity.json').write_text(json.dumps(result,indent=2)+'\n')
    (root/'sha256-manifest.json').write_text(json.dumps(index,indent=2)+'\n')
    print(json.dumps({'checks':checks,'indexed_files':len(index)}))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__=='__main__': main()
