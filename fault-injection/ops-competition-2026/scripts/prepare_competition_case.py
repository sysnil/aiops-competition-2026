#!/usr/bin/env python3
"""Register the only contest case after both actual 300-second qualifications."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
from competition_observation import ROOT,sha,save

def main():
    p=argparse.ArgumentParser();p.add_argument('--inspection',type=Path,required=True);a=p.parse_args()
    telemetry_path=ROOT/'qualification/production-telemetry-readiness.json'
    telemetry=json.loads(telemetry_path.read_text())
    if telemetry.get('status')!='COMPETITION_PRODUCTION_TELEMETRY_PASS':
        raise RuntimeError('Production telemetry is blocked; never register an hour on unexplained capture loss')
    proofs=[]
    for layer in ('SERVICE','POD'):
        ident='COMP-SF01-'+layer+'-300';path=ROOT/'qualification'/ident/'decision.json'
        result=json.loads(path.read_text())
        if result.get('qualified') is not True or result['status']!='COMPETITION_'+layer+'_QUALIFIED':raise RuntimeError('300-second mechanism not qualified')
        proofs.append({'id':ident,'file':str(path),'sha256':sha(path)})
    lock=json.loads((ROOT/'plans/competition-source-lock-v1.json').read_text())
    sources=dict(lock['source_sha256'])
    for name in ('competition_case_observation.py','competition_case_baseline.py','competition_case_preflight.py',
                 'refresh_case_safety.py','prepare_competition_case.py','run_competition_case.py','validate_contest_capture.py'):
        path=ROOT/'scripts'/name;sources[str(path)]=sha(path)
    for path,digest in sources.items():
        if sha(path)!=digest:raise RuntimeError('Qualified source changed')
    rows=json.loads(a.inspection.read_text())['targets'];public=[]
    for row in rows:
        if len(row['processes'])!=1 or row['processes'][0]['pid']!=row['container_task_pid']:raise RuntimeError('Target process not unique')
        if not row['conservative_32MiB_plus_32MiB_overhead_fits_80pct']:raise RuntimeError('Memory safety precondition failed')
        public.append({k:row[k] for k in ('service','name','uid','node','container_id','restart_count')})
    common={'at_utc':datetime.now(timezone.utc).isoformat(),'source_sha256':sources,'prerequisites':proofs,
        'production_telemetry_proof_sha256':sha(telemetry_path),
        'contract_sha256':sha(ROOT/'plans/competition-fault-admission.yaml'),
        'adapter_sha256':sha(ROOT/'scripts/competition_adapter.py'),'holder_sha256':sha(ROOT/'scripts/resource_memory_holder.py'),
        'targets_and_siblings':public,'seed':910105,'allocation_bytes':33554432,
        'workload':{'duration_seconds':3600,'control_streams':[
            {'name':'image_asset','rate_per_second':1.0,'seed_offset':101},
            {'name':'cart_view','rate_per_second':2.0,'seed_offset':103}],
            'control_timeout_seconds':8,'control_concurrency_per_stream':{'image_asset':2,'cart_view':4}}}
    faults=[];pending=[]
    for layer,offset in [('SERVICE',720),('POD',2100)]:
        chosen=[r for r in rows if r['service']=='recommendation'] if layer=='SERVICE' else [r for r in rows if r['service']=='frontend' and r['node']=='k8s-worker2']
        if len(chosen)!=(2 if layer=='SERVICE' else 1):raise RuntimeError('Scope changed')
        targets=[]
        for r in chosen:
            t={k:r[k] for k in ('service','name','uid','node','container_id','restart_count','cgroup')}
            t.update({k:r['processes'][0][k] for k in ('pid','start_ticks')});targets.append(t)
        ident='SF-PILOT-01-'+layer
        item={**common,'id':ident,'layer':layer,'host':targets[0]['node'],'targets':targets,
              'duration_seconds':300,'offset_seconds':offset,'formal_case':True}
        path=ROOT/'plans'/(ident+'.json');pending.append((path,item))
        faults.append({'id':ident,'offset_seconds':offset,'duration_seconds':300,'plan':str(path)})
    plan={**common,'id':'SF-PILOT-01','public_id':'SF-001','duration_seconds':3600,'faults':faults,
        'status':'CONTEST_CASE_PREREGISTERED_NOT_RESEARCH_FREEZE','capture_chunk_seconds':600,
        'trace_snapshot_offsets_seconds':[5,60,120,240],'formal_case':True,
        'normal_workload_strategy_unchanged_across_all_phases':True,
        'qualification_proofs_both_pass':True,'inspection_sha256':sha(a.inspection)}
    destination=ROOT/'plans/SF-PILOT-01.json'
    if destination.exists() or any(p.exists() for p,_ in pending):raise RuntimeError('Never overwrite registration')
    for path,item in pending:save(path,item)
    plan['fault_plan_sha256']={str(path):sha(path) for path,_ in pending};save(destination,plan)
    print(json.dumps({'status':plan['status'],'sha256':sha(destination)}))

if __name__=='__main__':main()
