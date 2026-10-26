"""Configuration-driven 2–6 event controller; harmless entry only. No real fault backend."""
import argparse,json,time,subprocess,signal,os
from pathlib import Path
from datetime import datetime,timezone
from run_registry import ROOT,load_bundle,sha,stamp,action_for
from handshake import Gate,atomic,digest
from master_transport import remote,copy,put,optional,clock

def now():return datetime.now(timezone.utc).isoformat()
def read(p):return json.loads(Path(p).read_text())
def cmd(argv,timeout=90):
 p=subprocess.run(list(map(str,argv)),capture_output=True,text=True,timeout=timeout)
 if p.returncode:raise RuntimeError(p.stdout[-800:]+p.stderr[-1500:])
 return p.stdout

def main():
 p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--manifest-sha',required=True);args=p.parse_args();m,plan=load_bundle(args.manifest,args.manifest_sha)
 if __import__('socket').gethostname()!='k8s-master':raise RuntimeError('MASTER_ONLY')
 if any(sha(f)!=v for f,v in plan['source_sha256'].items()):raise RuntimeError('SOURCE_CHANGED')
 control=Path(plan['control_root']);atomic(control/'started.json',{'at_utc':now(),'formal_case':False,'business_faults_injected':0},True)
 result={'status':'MARKER_INTEGRATION_FAILED','formal_cases_created':0,'business_faults_injected':0,'events':{},'negative_tests':{}}
 armed=[];granted={};done=set();onsets={};clocks={};last_clock=0;gate=None;pid=None;observer_started=False
 def host(a):return 'w3'
 def event(phase,**kw):
  with (control/'workflow.jsonl').open('a') as f:f.write(json.dumps({'at_utc':now(),'phase':phase,**kw})+'\n')
 def backend(a,verb,digest_=args.manifest_sha,h=None):return remote(h or host(a),['python3','-B',ROOT/'scripts/marker_worker.py',verb,'--manifest',args.manifest,'--manifest-sha',digest_,'--action-id',a['action_id']])
 def refresh(tag,pending):
  command=['python3','-B',ROOT/'scripts/marker_preflight.py','--tag',tag,'--manifest',args.manifest,'--manifest-sha',args.manifest_sha]
  for a in pending:command+=['--require-pending',a['action_id']]
  event('PREFLIGHT_BEGIN',tag=tag);cmd(command);event('PREFLIGHT_PASS',tag=tag)
  return control/('preflight-'+tag+'.json')
 def check():
  nonlocal clocks,last_clock
  alive=int(cmd(['systemctl','show',m['observer_unit']+'.service','-p','MainPID','--value']).strip() or 0)==pid
  hb=read(control/'heartbeat.json') if (control/'heartbeat.json').exists() else None
  if not gate.accept(hb,time.time(),pid,alive):raise RuntimeError('NOT_READY')
  if sum(f.stat().st_size for f in control.rglob('*') if f.is_file())>plan['marker_metadata_budget_bytes']:raise RuntimeError('MARKER_METADATA_BUDGET')
  if time.monotonic()-last_clock>15:
   clocks={h:clock(h,3) for h in ('w3',)};last_clock=time.monotonic()
   if any(abs(v['worker_minus_master_seconds'])+v['uncertainty_seconds']>.5 for v in clocks.values()):raise RuntimeError('CLOCK_BOUND')
  for a in armed:
   ident=a['action_id'];start=stamp(a['planned_utc']);r=Path(a['runtime']);h=host(a)
   if ident in done:continue
   if time.time()>=start-2:
    onset=optional(h,r/'actual-start.json');terminal=optional(h,r/'terminal.json')
    if onset and ident not in onsets:
     onset['seen_master_utc']=now();onset['feedback_upper_seconds']=time.time()-stamp(onset['actual_worker_utc'])+clocks[h]['worker_minus_master_seconds']+clocks[h]['uncertainty_seconds']
     if not onset['pass'] or onset['feedback_upper_seconds']>2:raise RuntimeError('ONSET_OR_FEEDBACK')
     onsets[ident]=onset;atomic(control/(ident+'-onset.json'),onset,True);event('ONSET_VERIFIED',action=ident)
    if time.time()>start+2.5 and not onset:raise RuntimeError('MISSING_ONSET')
    if terminal:
     if terminal['status']!='MARKER_COMPLETE' or not 3<=terminal.get('duration_seconds',0)<=4:raise RuntimeError('MARKER_FAILED:'+str(terminal))
     result['events'][ident]=terminal
    elif time.time()>start+10:raise RuntimeError('MISSING_TERMINAL')
   else:terminal=None
   if ident in granted and not terminal:
    issue=time.time();put(h,r/'lease.json',{'run_id':m['run_id'],'action_id':ident,'manifest_sha256':args.manifest_sha,'permission':'MARKER_ONLY','authorization_sha256':granted[ident],'issued_utc':now(),'expires_utc':datetime.fromtimestamp(issue+4,timezone.utc).isoformat(),'clock':clocks[h],'safety_pass':True,'heartbeat_sequence':hb['sequence'],'heartbeat_sha256':digest(hb)},True)
 def wait(until):
  while time.time()<until:check();time.sleep(min(.2,max(0,until-time.time())))
 for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):signal.signal(sig,lambda n,f:(_ for _ in ()).throw(SystemExit(128+n)))
 try:
  health=read(plan['health_file'])
  if sha(plan['health_file'])!=plan['health_sha256'] or health['status']!='SF009_DRYRUN_ENVIRONMENT_PASS' or health['current_environment_blockers'] or not 0<=time.time()-stamp(health['at_utc'])<=180:raise RuntimeError('FRESH_HEALTH_REQUIRED')
  for h in ('w3',):
   remote(h,['mkdir','-p',ROOT/'scripts',Path(args.manifest).parent])
   for name in ('run_registry.py','handshake.py','marker_worker.py'):copy(h,ROOT/'scripts'/name,ROOT/'scripts'/name)
   for file in (m['plan_path'],m['schedule_lock_path'],args.manifest):copy(h,file,file)
  for label,h,a,hash_ in [('wrong_hash',host(m['actions'][0]),m['actions'][0],'0'*64)]:
   try:backend(a,'arm',hash_,h)
   except RuntimeError as ex:
    expected='REGISTRATION_FILE_HASH'
    if expected not in str(ex):raise
    result['negative_tests'][label]={'rejected':True,'error':str(ex)[-600:]}
   else:raise RuntimeError('NEGATIVE_ARM_WAS_ACCEPTED')
  try:action_for(m,m['actions'][0]['action_id'],'k8s-worker2')
  except ValueError as ex:
   if 'ACTION_NOT_ON_WORKER' not in str(ex):raise
   result['negative_tests']['wrong_worker']={'rejected':True,'error':str(ex)}
  else:raise RuntimeError('WRONG_WORKER_ACCEPTED')
  began=time.time();cmd(['systemd-run','--unit='+m['observer_unit'],'--setenv=SF009_RUN=/data/stage5-v3/ops-competition-2026/rehearsals/SF009-REVERSE-NOFAULT-01','--property=RuntimeMaxSec=1200','python3','-B',ROOT/'scripts/marker_observer.py','--manifest',args.manifest,'--manifest-sha',args.manifest_sha]);observer_started=True
  while not (control/'observer-pid.json').exists():
   if time.time()-began>=15:raise RuntimeError('OBSERVER_INIT_TIMEOUT')
   time.sleep(.05)
  pid=read(control/'observer-pid.json')['pid'];gate=Gate(m['run_id'],plan['run_token'],m['plan_sha256'],plan['source_sha256'][str(ROOT/'scripts/marker_observer.py')],began,stamp(plan['start_utc']))
  while True:
   hb=read(control/'heartbeat.json') if (control/'heartbeat.json').exists() else None
   alive=int(cmd(['systemctl','show',m['observer_unit']+'.service','-p','MainPID','--value']).strip() or 0)==pid
   if gate.accept(hb,time.time(),pid,alive):break
   time.sleep(.05)
  atomic(control/'ready.json',{'at_utc':now(),'startup_seconds':time.time()-began,'heartbeat_sha256':digest(hb)},True);event('READY')
  for a in m['actions']:armed.append(a);backend(a,'arm');event('TIMER_ARMED',action=a['action_id'])
  refresh('ARMED',m['actions'])
  if time.time()>=stamp(plan['start_utc']):raise RuntimeError('READY_BEFORE_T0_MISSED')
  for i,a in enumerate(m['actions']):
   ident=a['action_id'];start=stamp(a['planned_utc']);wait(start-35);health_path=refresh('E'+str(i+1),m['actions'][i:]);check()
   if time.time()>=start-5:raise RuntimeError('PREFLIGHT_TOO_LATE')
   auth={'run_id':m['run_id'],'action_id':ident,'manifest_sha256':args.manifest_sha,'permission':'MARKER_ONLY','business_fault_permission':False,'at_utc':now(),'health_sha256':sha(health_path),'heartbeat_sha256':sha(control/'heartbeat.json')}
   path=control/(ident+'-authorization.json');atomic(path,auth,True);copy(host(a),path,Path(a['runtime'])/'authorization.json');granted[ident]=sha(path);event('AUTHORIZED',action=ident)
   wait(start+6)
   if ident not in result['events']:raise RuntimeError('EVENT_HAS_NO_COMPLETION')
   granted.pop(ident,None);backend(a,'cancel');done.add(ident);event('MARKER_CLEANED',action=ident)
   copy(host(a),a['runtime'],control/(ident+'-worker'),True,True)
   try:backend(a,'arm')
   except RuntimeError as ex:
    if 'CANNOT_REARM_CANCELLED_OR_STARTED' not in str(ex):raise
    result['events'][ident]['rearm_rejected']=True
   else:raise RuntimeError('CANCELLED_MARKER_REARMED')
  result['onsets']=onsets;result['status']='SF009_TWO_EVENT_MARKER_SCHEDULER_PASS'
 except BaseException as ex:result['error']=type(ex).__name__+': '+str(ex);event('ABORTING',error=result['error'])
 finally:
  granted.clear()
  for a in armed:
   try:backend(a,'cancel')
   except Exception as ex:result.setdefault('cleanup_errors',[]).append(str(ex));result['status']='MARKER_INTEGRATION_FAILED'
  if observer_started:subprocess.run(['systemctl','stop',m['observer_unit']+'.service'],capture_output=True,timeout=25)
  try:result['final_health']=str(refresh('POST',[]))
  except Exception as ex:result['final_health_error']=str(ex);result['status']='MARKER_INTEGRATION_FAILED'
  result['completed_utc']=now();result['not_validated']=['real fault adapters','business fault recovery','formal 30-minute capture/export/admission'];atomic(control/'terminal.json',result,True);print(json.dumps(result),flush=True)
 if result['status']!='SF009_TWO_EVENT_MARKER_SCHEDULER_PASS':raise SystemExit(1)
if __name__=='__main__':main()
