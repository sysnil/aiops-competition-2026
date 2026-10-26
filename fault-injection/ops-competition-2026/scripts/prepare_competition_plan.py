#!/usr/bin/env python3
"""Lock competition sources and bind one previously allowed qualification attempt."""

import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

from competition_observation import (ROOT,OLD,CG,STATE,CONTROLS,MACHINE,PROFILES,PEOPLE,QUALIFICATIONS)

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
    p.chmod(0o600)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--lock',action='store_true')
    parser.add_argument('--id',choices=QUALIFICATIONS);parser.add_argument('--inspection',type=Path)
    parser.add_argument('--prerequisite',type=Path);args=parser.parse_args()
    lockpath=ROOT/'plans/competition-source-lock-v1.json'
    if args.lock:
        paths=[ROOT/'plans/competition-fault-admission.yaml',
            *[ROOT/'scripts'/name for name in ('competition_adapter.py','resource_memory_holder.py',
              'competition_observation.py','competition_controls.py','competition_preflight.py',
              'competition_qualification.py','competition_baseline_prefix.py',
              'authorize_competition.py','prepare_competition_plan.py',
              'run_competition_qualification.py')],
            STATE,MACHINE,PROFILES,PEOPLE,OLD/'fixed_control_load_c3.py',
            OLD/'capture_live_c2.py',OLD/'trace_storage_reader.py',
            CG/'schedule_trace_completion_v2.py',CG/'schedule_trace_v2.py',CG/'trace_identity.py',
            CG/'evaluate_trace_v2.py']
        value={'status':'COMPETITION_ADMISSION_SOURCE_LOCKED_NOT_RESEARCH_FREEZE',
            'at_utc':datetime.now(timezone.utc).isoformat(),
            'source_sha256':{str(p):sha(p) for p in paths},'faults_injected':0,
            'allowed_qualification_ids':list(QUALIFICATIONS),'maximum_qualification_faults':4}
        save(lockpath,value);print(json.dumps({'status':value['status'],'sha256':sha(lockpath)}));return
    lock=read(lockpath)
    for path,digest in lock['source_sha256'].items():
        if sha(path)!=digest:raise RuntimeError('Locked competition source changed: '+path)
    rows=read(args.inspection)['targets']
    public=[]
    for row in rows:
        if len(row['processes'])!=1 or row['processes'][0]['pid']!=row['container_task_pid']:
            raise RuntimeError('Candidate business process scope not unique')
        public.append({k:row[k] for k in ('service','name','uid','node','container_id','restart_count')})
    layer='SERVICE' if '-SERVICE-' in args.id else 'POD'
    chosen=[r for r in rows if r['service']=='recommendation'] if layer=='SERVICE' else [r for r in rows if r['service']=='frontend' and r['node']=='k8s-worker2']
    expected=2 if layer=='SERVICE' else 1
    if len(chosen)!=expected:raise RuntimeError('Target count changed')
    targets=[]
    for row in chosen:
        if not row['conservative_32MiB_plus_32MiB_overhead_fits_80pct']:raise RuntimeError('Target memory reserve not sufficient')
        t={k:row[k] for k in ('service','name','uid','node','container_id','restart_count','cgroup')}
        t.update({k:row['processes'][0][k] for k in ('pid','start_ticks')});targets.append(t)
    durations=QUALIFICATIONS[args.id]
    seed={'COMP-SF01-SERVICE-SHORT':910101,'COMP-SF01-SERVICE-300':910102,
          'COMP-SF01-POD-SHORT':910103,'COMP-SF01-POD-300':910104}[args.id]
    prereqs=[]
    if args.id.endswith('-300'):
        if not args.prerequisite:raise RuntimeError('Short qualification proof required')
        proof=read(args.prerequisite)
        expected_id='COMP-SF01-'+layer+'-SHORT'
        if proof.get('id')!=expected_id or proof.get('qualified') is not True:raise RuntimeError('Short qualification not passed')
        prereqs=[{'id':expected_id,'file':str(args.prerequisite),'sha256':sha(args.prerequisite)}]
    plan={'id':args.id,'status':'PRIVATE_COMPETITION_QUALIFICATION_PREREGISTERED',
        'at_utc':datetime.now(timezone.utc).isoformat(),'layer':layer,'host':targets[0]['node'],
        'targets':targets,'targets_and_siblings':public,'durations_seconds':list(durations),
        'duration_seconds':durations[1],'seed':seed,'allocation_bytes':32*1024**2,
        'contract_sha256':sha(ROOT/'plans/competition-fault-admission.yaml'),
        'adapter_sha256':sha(ROOT/'scripts/competition_adapter.py'),
        'holder_sha256':sha(ROOT/'scripts/resource_memory_holder.py'),
        'source_lock_sha256':sha(lockpath),'source_sha256':lock['source_sha256'],
        'inspection_sha256':sha(args.inspection),'prerequisites':prereqs,
        'workload':{'duration_seconds':sum(durations),'control_streams':[
            {'name':'image_asset','rate_per_second':1.0,'seed_offset':101},
            {'name':'cart_view','rate_per_second':2.0,'seed_offset':103}],
            'control_timeout_seconds':8,'control_concurrency_per_stream':{'image_asset':2,'cart_view':4}},
        'formal_case':False}
    out=ROOT/'plans'/(args.id+'.json');save(out,plan)
    print(json.dumps({'status':plan['status'],'id':args.id,'sha256':sha(out)}))

if __name__=='__main__':main()
