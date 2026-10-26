#!/usr/bin/env python3
"""Start state-machine and fixed ordinary-control streams on one UTC schedule."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

import yaml


ROOT=Path('/root/stage5-v3-autonomous-preparation')
STATE=Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py')
CONTROLS=Path('/root/cross-layer-observability-control-rehearsal/scripts/fixed_control_load.py')
PEOPLE=Path('/root/cross-layer-workload-generation/design/people.json')
PROFILES=ROOT/'design/load-profiles-c2.yaml'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--health',type=Path,required=True)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--seed',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--allow-errors',action='store_true')
    args=parser.parse_args()
    if args.output.exists() or not args.run_id.startswith('S5V3AP-'):
        raise ValueError('A new independent qualification run ID and output are required')
    plan=yaml.safe_load(args.plan.read_text())
    if plan.get('revision') != 'autonomous_preparation_clean_c2':
        raise ValueError('Workload plan is not the qualification revision')
    health=json.loads(args.health.read_text())
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(health['at_utc'])).total_seconds()
    if health.get('status')!='STAGE5_BASE_HEALTH_PASS' or not 0<=age<=180:
        raise ValueError('No fresh clean health gate')
    args.output.mkdir(parents=True)
    scheduled=datetime.now(timezone.utc)+timedelta(seconds=20)
    duration=plan['workload']['duration_seconds']
    if (duration != 1500 or plan['workload']['before_seconds'] != 600 or
            plan['workload']['during_seconds'] != 300 or
            plan['workload']['after_seconds'] != 600):
        raise ValueError('Qualification requires the full 10+5+10 minute schedule')
    schedule={'run_id':args.run_id,'seed':args.seed,
              'scheduled_start_utc':scheduled.isoformat(),
              'before_end_utc':(scheduled+timedelta(seconds=600)).isoformat(),
              'during_end_utc':(scheduled+timedelta(seconds=900)).isoformat(),
              'scheduled_end_utc':(scheduled+timedelta(seconds=duration)).isoformat(),
              'plan_sha256':sha(args.plan),'health_sha256':sha(args.health),
              'state_machine_sha256':sha(STATE),'control_generator_sha256':sha(CONTROLS),
              'fault_injected_by_load_pair':False}
    (args.output/'schedule.json').write_text(json.dumps(schedule,indent=2)+'\n')
    base='http://10.104.177.128:8080'
    command1=['python3',str(STATE),'--machine',plan['workload']['machine'],
        '--profiles',str(PROFILES),'--people',str(PEOPLE),'--profile','MEDIUM',
        '--seed',str(args.seed),'--duration',str(duration),'--start-utc',scheduled.isoformat(),
        '--base-url',base,'--output',str(args.output/'state-machine')]
    if args.allow_errors:command1.append('--allow-errors')
    command2=['python3',str(CONTROLS),'--plan',str(args.plan),'--seed',str(args.seed),
        '--duration',str(duration),'--start-utc',scheduled.isoformat(),
        '--base-url',base,'--output',str(args.output/'controls')]
    processes=[]
    try:
        for name,command in (('state-machine',command1),('controls',command2)):
            stdout=(args.output/(name+'.stdout')).open('x')
            stderr=(args.output/(name+'.stderr')).open('x')
            processes.append((name,subprocess.Popen(command,stdout=stdout,stderr=stderr),stdout,stderr))
        failures=[]
        for name,process,stdout,stderr in processes:
            code=process.wait(timeout=duration+100)
            stdout.close();stderr.close()
            if code:failures.append(name+':exit='+str(code))
        manifests={}
        for name in ('state-machine','controls'):
            path=args.output/name/'result-manifest.json'
            if path.exists():manifests[name]=json.loads(path.read_text())
            else:failures.append(name+':manifest_missing')
        result={'status':'LOAD_PAIR_COMPLETE' if not failures else 'LOAD_PAIR_INCOMPLETE',
            'run_id':args.run_id,'completed_at_utc':datetime.now(timezone.utc).isoformat(),
            'failures':failures,'manifest_status':{name:row['status'] for name,row in manifests.items()}}
        (args.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result),flush=True)
        if failures:raise SystemExit(1)
    finally:
        for _,process,stdout,stderr in processes:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=15)
                except subprocess.TimeoutExpired:process.kill()
            if not stdout.closed:stdout.close()
            if not stderr.closed:stderr.close()


if __name__=='__main__':main()
