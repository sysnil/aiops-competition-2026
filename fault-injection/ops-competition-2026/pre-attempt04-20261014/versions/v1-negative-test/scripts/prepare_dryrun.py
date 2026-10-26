import argparse,json,time,secrets
from datetime import datetime,timezone
from pathlib import Path
from run_registry import ROOT,BACKEND,sha,load_bundle
from handshake import atomic
import sys
sys.path.insert(0,'/root/ops-competition-2026/scripts')
from competition_observation import storage
def read(p):return json.loads(Path(p).read_text())
def utc(t):return datetime.fromtimestamp(t,timezone.utc).isoformat()
def main():
    p=argparse.ArgumentParser();p.add_argument('--run-id',required=True);p.add_argument('--health',required=True);p.add_argument('--inspection',required=True);p.add_argument('--negative',action='store_true');a=p.parse_args()
    health=read(a.health)
    if health['current_environment_blockers'] or health['status']!='COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING' or not 0<=time.time()-datetime.fromisoformat(health['at_utc']).timestamp()<=120:raise RuntimeError('FRESH_SAFETY_REQUIRED')
    storage();old=read('/root/ops-competition-2026/plans/SF-PILOT-01-ATTEMPT-03.json');current=read(a.inspection)['targets']
    for t in old['targets_and_siblings']:
        if not any(r['uid']==t['uid'] and r['container_id']==t['container_id'] and r['node']==t['node'] for r in current):raise RuntimeError('TARGET_IDENTITY_CHANGED')
    folder=ROOT/'plans'/a.run_id
    if folder.exists():raise RuntimeError('PRESERVE_ALL_RUNS')
    folder.mkdir(parents=True,mode=0o700);planpath=folder/'run-plan.json';start=time.time()+180
    sources={str(f):sha(f) for f in (ROOT/'scripts').glob('*.py')}
    sources.update(read('/root/ops-competition-2026/plans/competition-source-lock-v1.json')['source_sha256'])
    if any(sha(f)!=v for f,v in sources.items()):raise RuntimeError('SOURCES_CHANGED')
    actions=[]
    for layer,worker,offset in [('SERVICE','k8s-worker3',660),('POD','k8s-worker2',840)]:
        qual=read('/root/ops-competition-2026/plans/COMP-SF01-'+layer+'-300.json');targets=[]
        for t in qual['targets']:
            live=next(r for r in current if r['uid']==t['uid'])
            if len(live['processes'])!=1 or any(str(live['processes'][0][k])!=str(t[k]) for k in ('pid','start_ticks')):raise RuntimeError('PROCESS_CHANGED')
            targets.append(t)
        ident=a.run_id+'-'+layer
        actions.append({'run_id':a.run_id,'action_id':ident,'mechanism_id':layer,'worker':worker,
          'targets':targets,'planned_utc':utc(start+offset),'offset_seconds':offset,'duration_seconds':10,
          'unit':'ops-p04-'+ident.lower(),'runtime':str(ROOT/'worker-runtime'/ident),
          'registration_file':str(planpath),'backend':BACKEND,'execute':'WRITE_MARKER_AND_MONITOR_LEASE',
          'revoke':'REVOKE_AND_STOP_OWN_TIMER','onset_tolerance_seconds':2})
    data=Path('/data/stage5-v3/ops-competition-2026/pre-attempt04')
    plan={**old,'id':a.run_id,'run_token':secrets.token_hex(24),'backend':BACKEND,'formal_case':False,
      'actions':actions,'faults':[],'markers':[],'source_sha256':sources,'duration_seconds':900,
      'start_utc':utc(start),'capture_offsets_seconds':[0,300,600,900],
      'raw_root':str(data/'runs'/a.run_id),'control_root':str(data/'runtime'/a.run_id),
      'controller_unit':'ops-p04-controller-'+a.run_id.lower(),'observer_unit':'ops-p04-observer-'+a.run_id.lower(),
      'health_file':a.health,'health_sha256':sha(a.health),'seed':int(time.time())%10000000,
      'workload':{**old['workload'],'duration_seconds':900},'negative_scenario':'INVALID_SCHEDULE_LOCK_HASH' if a.negative else None,
      'collector_uid':old['collector_uid'],'initialization_budget_seconds':15,'heartbeat_freshness_seconds':30}
    atomic(planpath,plan,True)
    lock={'run_id':a.run_id,'plan_sha256':sha(planpath),'actions':actions};lockpath=folder/'schedule-lock.json';atomic(lockpath,lock,True)
    manifest={'schema':'EXPLICIT_RUN_REGISTRY_V1','run_id':a.run_id,'backend':BACKEND,'plan_path':str(planpath),'plan_sha256':sha(planpath),
      'schedule_lock_path':str(lockpath),'schedule_lock_sha256':sha(lockpath),'actions':actions,
      'controller_unit':plan['controller_unit'],'observer_unit':plan['observer_unit']}
    path=folder/'run-manifest.json';atomic(path,manifest,True);load_bundle(path,sha(path))
    if a.negative:atomic(folder/'negative-manifest.json',{**manifest,'schedule_lock_sha256':'0'*64},True)
    print(json.dumps({'run_id':a.run_id,'manifest':str(path),'manifest_sha256':sha(path),'start_utc':plan['start_utc'],'actions':[{'id':x['action_id'],'utc':x['planned_utc']} for x in actions]}))
if __name__=='__main__':main()
