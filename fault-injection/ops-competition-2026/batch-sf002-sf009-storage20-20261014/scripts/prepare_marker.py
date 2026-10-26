"""Six harmless events, NOT a formal Case and NOT a production authorization."""
import json,time,secrets,argparse
from pathlib import Path
from datetime import datetime,timezone
from run_registry import ROOT,BACKEND,sha,load_bundle
from handshake import atomic

def main():
 q=argparse.ArgumentParser();q.add_argument('--health',required=True);a=q.parse_args();h=json.loads(Path(a.health).read_text())
 if h['current_environment_blockers'] or not 0<=time.time()-datetime.fromisoformat(h['at_utc']).timestamp()<=120:raise RuntimeError('FRESH_ENVIRONMENT_REQUIRED')
 ident='B8-MARKER-SIX-01';folder=ROOT/'marker-plans'/ident
 if folder.exists():raise RuntimeError('NO_OVERWRITE')
 folder.mkdir(parents=True,mode=0o700);pp=folder/'plan.json';start=time.time()+120
 utc=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat()
 actions=[]
 for i in range(6):
  aid=f'{ident}-E{i+1:02d}';offset=45*(i+1)
  actions.append({'run_id':ident,'action_id':aid,'worker':'k8s-worker'+str(i%3+1),'unit':'ops-b8-'+aid.lower(),'backend':BACKEND,'execute':'WRITE_LOCAL_TIME_MARKER','revoke':'REVOKE_AND_CANCEL_MARKER','targets':[],
   'offset_seconds':offset,'planned_utc':utc(start+offset),'duration_seconds':3,'onset_tolerance_seconds':2,'registration_file':str(pp),'runtime':str(ROOT/'worker-runtime'/aid)})
 old=json.loads(Path('/root/ops-competition-2026/attempt04-20261014/plans/SF-PILOT-01-ATTEMPT-04/run-plan.json').read_text())
 plan={'id':ident,'backend':BACKEND,'formal_case':False,'business_fault_permission':False,'run_token':secrets.token_hex(24),'actions':actions,'start_utc':utc(start),'duration_seconds':300,'health_file':a.health,'health_sha256':sha(a.health),
 'control_root':'/data/stage5-v3/ops-competition-2026/batch-sf002-sf009/runtime/'+ident,'controller_unit':'ops-b8-controller-'+ident.lower(),'observer_unit':'ops-b8-observer-'+ident.lower(),
 'collector_uid':old['collector_uid'],'targets_and_siblings':old['targets_and_siblings'],'source_sha256':{str(f):sha(f) for f in (ROOT/'scripts').glob('*.py')},
 'scope':'scheduler integration only; no business fault, no new load, no multimodal case capture, no public package or GT','marker_metadata_budget_bytes':64*1024**2}
 atomic(pp,plan,True);lp=folder/'schedule-lock.json';atomic(lp,{'run_id':ident,'plan_sha256':sha(pp),'actions':actions},True)
 mp=folder/'run-manifest.json';m={'schema':'EXPLICIT_RUN_REGISTRY_V1','run_id':ident,'backend':BACKEND,'plan_path':str(pp),'plan_sha256':sha(pp),'schedule_lock_path':str(lp),'schedule_lock_sha256':sha(lp),'actions':actions,'controller_unit':plan['controller_unit'],'observer_unit':plan['observer_unit']};atomic(mp,m,True);load_bundle(mp,sha(mp))
 print(json.dumps({'manifest':str(mp),'manifest_sha256':sha(mp),'start_utc':plan['start_utc'],'events':len(actions),'business_fault_permission':False}))
if __name__=='__main__':main()
