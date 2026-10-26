"""Register a new fixed window; never change previous plans or retry a run ID."""
import argparse,json,time,secrets
from datetime import datetime,timezone
from pathlib import Path
from pilot_common import OPS,PROD,DATA,telemetry_state,sha,read,save,OFFSETS,stamp
from competition_observation import storage
def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['REHEARSAL','EXIT_TEST','FORMAL'],required=True);p.add_argument('--health',type=Path,required=True);p.add_argument('--inspection',type=Path,required=True);a=p.parse_args()
    h=read(a.health)
    if h['current_environment_blockers'] or h['status']!='COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING' or not 0<=time.time()-stamp(h['at_utc'])<=90:raise RuntimeError('Fresh safety required')
    state=telemetry_state();storage()
    if state['refused']!={'grpc':0.0,'http':0.0}:raise RuntimeError('Receiver refusals since verified rollout')
    ids={'REHEARSAL':'A03-NOFAULT-INTEGRATION','EXIT_TEST':'A03-OBSERVER-EXIT-TEST','FORMAL':'SF-PILOT-01-ATTEMPT-03'}
    ident=ids[a.mode];out=OPS/'plans'/(ident+'.json')
    if out.exists() or (DATA/'runs'/ident).exists():raise RuntimeError('Never overwrite run')
    if a.mode=='FORMAL' and read(PROD/'results/joint-rehearsal-verdict.json')['status']!='JOINT_REHEARSAL_PASS':raise RuntimeError('Joint test not PASS')
    old=read(OPS/'plans/SF-PILOT-01-ATTEMPT-02.json');inspection=read(a.inspection)['targets']
    for row in old['targets_and_siblings']:
        matches=[x for x in inspection if x['uid']==row['uid'] and x['container_id']==row['container_id'] and x['node']==row['node']]
        if len(matches)!=1 or not matches[0]['conservative_32MiB_plus_32MiB_overhead_fits_80pct']:raise RuntimeError('Target identity/headroom changed')
    sources=read(OPS/'plans/competition-source-lock-v1.json')['source_sha256']
    if any(sha(k)!=v for k,v in sources.items()):raise RuntimeError('Original qualified sources changed')
    sources.update({str(f):sha(f) for f in (PROD/'scripts').glob('*.py')})
    formal=a.mode=='FORMAL';duration=3600 if formal else 300
    plan={**old,'id':ident,'mode':a.mode,'run_token':secrets.token_hex(24),'source_sha256':sources,
          'seed':{'REHEARSAL':1009100,'EXIT_TEST':1011100,'FORMAL':1013100}[a.mode],
          'duration_seconds':duration,'start_utc':datetime.fromtimestamp(time.time()+150,timezone.utc).isoformat(),
          'health_file':str(a.health),'health_sha256':sha(a.health),'formal_case':formal,
          'capture_offsets_seconds':OFFSETS if formal else [0,300],
          'observer_unit':'ops-case-sf-pilot-01-attempt-03'+('' if formal else '-rehearsal' if a.mode=='REHEARSAL' else '-exit-test'),
          'initialization_budget_seconds':15,'heartbeat_freshness_seconds':30,'heartbeat_period_seconds':15,
          'prestart_ready_required':True,'collector_uid':state['uid'],'faults':[],
          'workload':{**old['workload'],'duration_seconds':duration},'markers':[],
          'authorized_new_formal_attempts':1,'automatic_attempt04':False}
    plan.pop('current_admission_sha256',None)
    if formal:
        for layer,offset in [('SERVICE',720),('POD',2100)]:
            f=read(OPS/'plans'/('COMP-SF01-'+layer+'-300.json'))
            for target in f['targets']:
                r=next(x for x in inspection if x['uid']==target['uid'])
                if len(r['processes'])!=1 or any(str(r['processes'][0][k])!=str(target[k]) for k in ('pid','start_ticks')):raise RuntimeError('Target process identity changed')
            fid=ident+'-'+layer;fp=OPS/'plans'/(fid+'.json')
            item={**old,**f,'id':fid,'source_sha256':sources,'prerequisites':old['prerequisites'],'targets_and_siblings':old['targets_and_siblings'],'offset_seconds':offset,'duration_seconds':300}
            save(fp,item);plan['faults'].append({'id':fid,'offset_seconds':offset,'duration_seconds':300,'plan':str(fp),'sha256':sha(fp)})
    else:
        plan['markers']=[{'id':ident+'-W2','host':'w2','offset_seconds':75},{'id':ident+'-W3','host':'w3','offset_seconds':135}]
    save(out,plan);save(PROD/'plans'/(ident+'-manifest.json'),{'plan_sha256':sha(out),'source_sha256':sources,'health_sha256':sha(a.health),'start_utc':plan['start_utc']})
    print(json.dumps({'plan':str(out),'run_id':ident,'start_utc':plan['start_utc'],'mode':a.mode,'plan_sha256':sha(out)}))
if __name__=='__main__':main()
