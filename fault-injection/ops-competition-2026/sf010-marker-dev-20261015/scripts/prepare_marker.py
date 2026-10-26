"""Two harmless SF-010 timing markers; no business fault capability."""
import json,time,secrets,argparse
from pathlib import Path
from datetime import datetime,timezone
from run_registry import ROOT,BACKEND,sha,load_bundle
from handshake import atomic

def main():
 q=argparse.ArgumentParser();q.add_argument('--capture-schedule',required=True,type=Path);a=q.parse_args()
 schedule=json.loads(a.capture_schedule.read_text());start=datetime.fromisoformat(schedule['start_utc']).timestamp()
 if schedule['id']!='SF010-CONTROLLER-DRYRUN-02' or schedule['duration_seconds']!=1800 or not 120<=start+600-time.time()<=900:raise RuntimeError('CAPTURE_SCHEDULE_NOT_CURRENT_OR_BOUND')
 import sys
 sys.path.insert(0,'/root/ops-competition-2026/sf010-formal-20261015-v2/scripts')
 from sf010_safety import safety
 from pilot_common import telemetry_state
 identity=Path('/root/ops-competition-2026/sf010-formal-20261015-v2/plans/current-identity.json')
 rows=json.loads(identity.read_text())['targets'];expected=[{k:r[k] for k in ('service','name','uid','node','container_id','restart_count')} for r in rows]
 env=safety(expected);collector=telemetry_state()
 ident='SF010-MARKER-DRYRUN-01';folder=ROOT/'marker-plans'/ident
 if folder.exists():raise RuntimeError('NO_OVERWRITE')
 folder.mkdir(parents=True,mode=0o700);pp=folder/'plan.json'
 health=folder/'health.json';atomic(health,{'status':'SF010_DRYRUN_ENVIRONMENT_PASS','at_utc':datetime.now(timezone.utc).isoformat(),'current_environment_blockers':[],'environment':env,'collector_uid':collector['uid'],'collector_refused':collector['refused'],'identity_sha256':sha(identity)},True)
 utc=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat()
 actions=[]
 for i,offset in enumerate((600,960)):
  aid=f'{ident}-E{i+1:02d}'
  actions.append({'run_id':ident,'action_id':aid,'worker':'k8s-worker3','unit':'ops-sf010-marker-'+aid.lower(),'backend':BACKEND,'execute':'WRITE_LOCAL_TIME_MARKER','revoke':'REVOKE_AND_CANCEL_MARKER','targets':[],
   'offset_seconds':offset,'planned_utc':utc(start+offset),'duration_seconds':3,'onset_tolerance_seconds':2,'registration_file':str(pp),'runtime':str(ROOT/'worker-runtime'/aid)})
 plan={'id':ident,'backend':BACKEND,'formal_case':False,'business_fault_permission':False,'run_token':secrets.token_hex(24),'actions':actions,'start_utc':utc(start),'duration_seconds':1800,'health_file':str(health),'health_sha256':sha(health),
 'control_root':'/data/stage5-v3/ops-competition-2026/rehearsals/SF010-CONTROLLER-DRYRUN-02/marker-control','controller_unit':'ops-sf010-marker-controller-'+ident.lower(),'observer_unit':'ops-sf010-marker-observer-'+ident.lower(),
 'collector_uid':collector['uid'],'targets_and_siblings':expected,'source_sha256':{str(f):sha(f) for f in (ROOT/'scripts').glob('*.py')},
 'capture_schedule_sha256':sha(a.capture_schedule),'scope':'scheduler integration only; no business fault, no replacement for actual recovery','marker_metadata_budget_bytes':64*1024**2}
 atomic(pp,plan,True);lp=folder/'schedule-lock.json';atomic(lp,{'run_id':ident,'plan_sha256':sha(pp),'actions':actions},True)
 mp=folder/'run-manifest.json';m={'schema':'EXPLICIT_RUN_REGISTRY_V1','run_id':ident,'backend':BACKEND,'plan_path':str(pp),'plan_sha256':sha(pp),'schedule_lock_path':str(lp),'schedule_lock_sha256':sha(lp),'actions':actions,'controller_unit':plan['controller_unit'],'observer_unit':plan['observer_unit']};atomic(mp,m,True);load_bundle(mp,sha(mp))
 print(json.dumps({'manifest':str(mp),'manifest_sha256':sha(mp),'start_utc':plan['start_utc'],'events':len(actions),'business_fault_permission':False}))
if __name__=='__main__':main()
