#!/usr/bin/env python3
"""Issue one narrow, expiring competition permission from actual live evidence."""

import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

ROOT=Path('/root/ops-competition-2026')
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    parser=argparse.ArgumentParser()
    for name in ('plan','health','baseline','watchdog','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise RuntimeError('Authorization already exists')
    plan,h,b,w=map(read,(args.plan,args.health,args.baseline,args.watchdog))
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(h['at_utc'])).total_seconds()
    bage=(datetime.now(timezone.utc)-datetime.fromisoformat(b['at_utc'])).total_seconds()
    if (h['status']!='COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING' or h['current_environment_blockers'] or
        not 0<=age<=90 or not 0<=bage<=180 or b['status']!='COMPETITION_BASELINE_PREFIX_PASS' or b['id']!=plan['id'] or
        w['status']!='COMPETITION_WATCHDOG_SELFTEST_PASS' or w['adapter_sha256']!=plan['adapter_sha256']):
        raise RuntimeError('No current actual safety/coverage/watchdog authorization')
    if any(sha(Path(p))!=digest for p,digest in plan['source_sha256'].items()):
        raise RuntimeError('Competition registered source changed before injection')
    result={'status':'COMPETITION_PREFLIGHT_AUTHORIZED','at_utc':datetime.now(timezone.utc).isoformat(),
        'id':plan['id'],'registration_sha256':sha(args.plan),'faults_allowed':True,
        'actual_environment_pass':True,'independent_watchdog_pass':True,
        'watchdog_proof_file':str(args.watchdog),'watchdog_proof_sha256':sha(args.watchdog),
        'health_sha256':sha(args.health),'baseline_sha256':sha(args.baseline),
        'scope':'ONLY_THIS_COMPETITION_ATTEMPT_NOT_RESEARCH_OR_ANOTHER_CASE',
        'research_fault_permission_changed':False}
    args.output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with args.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    args.output.chmod(0o600);print(json.dumps({'status':result['status'],'id':plan['id']}))

if __name__=='__main__':main()
