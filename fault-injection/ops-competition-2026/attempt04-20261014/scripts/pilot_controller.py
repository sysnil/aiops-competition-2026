#!/usr/bin/env python3
"""Formal ATTEMPT04 entry; common validated workflow, separate pinned fault capability."""
import argparse,json,os,signal,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
from run_registry import ROOT,load_bundle,sha,stamp
from handshake import Gate,atomic,digest
from master_transport import remote,copy,optional,put,clock
from pilot_common import RUN,CONTROL,RUN_ID,MANIFEST_PATH,MANIFEST_SHA
def now():return datetime.now(timezone.utc).isoformat()
def read(p):return json.loads(Path(p).read_text())
def cmd(args,timeout=120):
    p=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise RuntimeError(p.stdout[-1000:]+p.stderr[-1500:])
    return p.stdout
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--manifest',required=True);parser.add_argument('--manifest-sha',required=True);args=parser.parse_args()
    if (args.manifest,args.manifest_sha)!=(MANIFEST_PATH,MANIFEST_SHA):raise RuntimeError('CONTROLLER_REGISTRY_DIVERGENCE')
    m,p=load_bundle(args.manifest,args.manifest_sha,RUN_ID)
    if __import__('socket').gethostname()!='k8s-master':raise RuntimeError('MASTER_ONLY')
    if any(sha(f)!=h for f,h in p['source_sha256'].items()):raise RuntimeError('SOURCE_HASH')
    if RUN.exists() or (CONTROL/'controller-started.json').exists():raise RuntimeError('NO_OVERWRITE')
    atomic(CONTROL/'controller-started.json',{'run_id':RUN_ID,'manifest_sha256':args.manifest_sha,'at_utc':now()},True)
    result={'run_id':RUN_ID,'status':'FORMAL_EXECUTION_FAILED','faults':{},'public_exam_package_created':False,'stages':{},'onsets':{}}
    os.environ.update({'A04_CAPTURE_ROOT':str(RUN),'A04_BUDGET_LEDGER':str(CONTROL/'json-byte-reservations'),'A04_JSON_CAP_BYTES':str(p['storage_budget']['capture_json_cap_bytes'])})
    armed=[];granted={};done=set();clocks={};last_clock=0;observer_started=False;gate=None;pid=None
    def event(phase,**details):
        with (CONTROL/'workflow.jsonl').open('a') as f:f.write(json.dumps({'at_utc':now(),'phase':phase,'run_id':RUN_ID,'manifest_sha256':args.manifest_sha,**details})+'\n')
    def worker(a):return 'w3' if a['worker']=='k8s-worker3' else 'w2'
    def backend(a,verb):
        return remote(worker(a),['python3','-B',ROOT/'scripts/fault_worker.py',verb,'--manifest',args.manifest,'--manifest-sha',args.manifest_sha,'--action-id',a['action_id']])
    def refresh(tag,required=(),override=None):
        source=args.manifest if override is None else str(override);source_sha=args.manifest_sha if override is None else sha(source)
        ident='COMP-SF01-'+RUN_ID+'-'+tag
        call=['python3','-B',ROOT/'scripts/master_refresh_safety.py','--id',ident,'--manifest',source,'--manifest-sha',source_sha]
        for a in required:call+=['--require-pending',a['action_id']]
        event('PREFLIGHT_BEGIN',tag=tag,registration=source)
        output=cmd(call);event('PREFLIGHT_PASS',tag=tag)
        return Path('/data/stage5-v3/ops-competition-2026/audits')/ident/'phase-a-decision.json'
    def observer_alive():return int(cmd(['systemctl','show',m['observer_unit']+'.service','-p','MainPID','--value']).strip() or 0)==pid
    def check():
        nonlocal clocks,last_clock
        ob=read(RUN/'observation-result.json') if (RUN/'observation-result.json').exists() else None
        if ob:
            if ob['status']!='CASE_OBSERVATION_COMPLETE_PENDING_DATA_QUALITY':raise RuntimeError('OBSERVER_ABORT:'+str(ob))
            return ob
        h=read(CONTROL/'heartbeat.json') if (CONTROL/'heartbeat.json').exists() else None
        if not gate.accept(h,time.time(),pid,observer_alive()):raise RuntimeError('NOT_READY_NO_LEASE')
        if any((RUN/'controls'/(name+'-failure.json')).exists() for name in ('image_asset','cart_view')):raise RuntimeError('ORDINARY_CONTROL_FAILURE')
        if time.monotonic()-last_clock>=15:
            clocks={name:clock(name,3) for name in ('w2','w3')};last_clock=time.monotonic()
            if any(abs(c['worker_minus_master_seconds'])+c['uncertainty_seconds']>.5 for c in clocks.values()):raise RuntimeError('CLOCK_BOUND')
            with (CONTROL/'clock.jsonl').open('a') as f:f.write(json.dumps(clocks)+'\n')
        for a in armed:
            ident=a['action_id'];host=worker(a);r=Path(a['runtime']);start=stamp(a['planned_utc'])
            if ident in done:continue
            actual=optional(host,r/'actual-start.json');terminal=optional(host,r/'terminal.json')
            if actual and ident not in result['onsets']:
                actual['seen_master_utc']=now();actual['feedback_upper_seconds']=time.time()-stamp(actual['actual_worker_utc'])+clocks[host]['worker_minus_master_seconds']+clocks[host]['uncertainty_seconds']
                result['onsets'][ident]=actual;atomic(CONTROL/(ident+'-onset.json'),actual,True)
                event('ONSET_VERIFIED',action=ident,details=actual)
                if not actual['pass'] or actual['feedback_upper_seconds']>2:raise RuntimeError('ONSET_OR_FEEDBACK_OUT_OF_BOUNDS')
            if time.time()>start+2.5 and not actual:raise RuntimeError('NO_START_FEEDBACK')
            if terminal:
                if terminal['status']!='FORMAL_FAULT_COMPLETE':raise RuntimeError('FORMAL_FAULT_FAILURE:'+str(terminal))
                result['stages'].setdefault(ident,{})['execution']=terminal
                injection=terminal['injection']
                if abs(injection['duration_seconds']-300)>2 or injection['cleanup']['status']!='COMPETITION_CLEANUP_ACTIONS_VERIFIED':raise RuntimeError('FAULT_DURATION_OR_CLEANUP')
                result['faults'][ident]=injection
            elif time.time()>start+a['duration_seconds']+20:raise RuntimeError('FAULT_TERMINAL_OVERDUE')
            if ident in granted and not terminal:
                issue=time.time();lease={'run_id':RUN_ID,'action_id':ident,'manifest_sha256':args.manifest_sha,
                  'permission':'BOUND_QUALIFIED_FAULT','authorization_sha256':granted[ident],'issued_utc':now(),
                  'expires_utc':datetime.fromtimestamp(issue+4,timezone.utc).isoformat(),'clock':clocks[host],
                  'safety_pass':True,'heartbeat_sequence':h['sequence'],'heartbeat_sha256':digest(h)}
                put(host,r/'lease.json',lease,True)
                with (CONTROL/'leases.jsonl').open('a') as f:f.write(json.dumps(lease)+'\n')
        return ob
    def wait(t):
        while time.time()<t:check();time.sleep(min(.25,max(0,t-time.time())))
    for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):signal.signal(sig,lambda n,f:(_ for _ in ()).throw(SystemExit(128+n)))
    try:
        health=read(p['health_file'])
        if sha(p['health_file'])!=p['health_sha256'] or health['current_environment_blockers'] or not 0<=time.time()-stamp(health['at_utc'])<=180:raise RuntimeError('INITIAL_SAFETY_INVALID')
        for host in ('w2','w3'):
            remote(host,['mkdir','-p',ROOT/'scripts',Path(args.manifest).parent])
            for name in ('run_registry.py','handshake.py','fault_worker.py'):copy(host,ROOT/'scripts'/name,ROOT/'scripts'/name)
            for path in (m['plan_path'],m['schedule_lock_path'],args.manifest):copy(host,path,path)
            for action in m['actions']:
                copy(host,action['qualified_registration'],action['qualified_registration'])
        launched=time.time();event('INITIALIZING')
        cmd(['systemd-run','--unit='+m['observer_unit'],'--setenv=KUBECONFIG=/etc/kubernetes/admin.conf',
             '--setenv=SF_RUN_MANIFEST='+args.manifest,'--setenv=SF_RUN_MANIFEST_SHA='+args.manifest_sha,'--setenv=A04_CAPTURE_ROOT='+str(RUN),'--setenv=A04_BUDGET_LEDGER='+str(CONTROL/'json-byte-reservations'),'--setenv=A04_JSON_CAP_BYTES='+str(p['storage_budget']['capture_json_cap_bytes']),
             '--property=RuntimeMaxSec=4380','python3','-B',ROOT/'scripts/pilot_observer.py','--manifest',args.manifest,'--manifest-sha',args.manifest_sha,'--health',p['health_file']]);observer_started=True
        while not (CONTROL/'initializing.json').exists():
            if time.time()-launched>=15:raise RuntimeError('FIRST_HEARTBEAT_TIMEOUT')
            time.sleep(.05)
        pid=read(CONTROL/'initializing.json')['pid'];gate=Gate(RUN_ID,p['run_token'],m['plan_sha256'],p['source_sha256'][str(ROOT/'scripts/pilot_observer.py')],launched,stamp(p['start_utc']))
        event('WAITING_FOR_FIRST_SAMPLE')
        while True:
            h=read(CONTROL/'heartbeat.json') if (CONTROL/'heartbeat.json').exists() else None
            if gate.accept(h,time.time(),pid,observer_alive()):break
            time.sleep(.05)
        receipt={'run_id':RUN_ID,'run_token':p['run_token'],'plan_sha256':m['plan_sha256'],'manifest_sha256':args.manifest_sha,'heartbeat_sha256':digest(h),'at_utc':now(),'startup_seconds':time.time()-launched}
        atomic(CONTROL/'ready-receipt.json',receipt,True);event('READY',startup_seconds=receipt['startup_seconds'])
        for a in m['actions']:
            armed.append(a);backend(a,'arm');event('TIMER_ARMED',action=a['action_id'])
        # This is the SAME registry/preflight code path later used before both phases.
        refresh('ARMED',m['actions'])
        if time.time()>=stamp(p['start_utc'])-5:raise RuntimeError('PRESTART_GATES_MISSED_T0')
        for index,a in enumerate(m['actions']):
            ident=a['action_id'];start=stamp(a['planned_utc']);remaining=m['actions'][index:]
            wait(start-105);health_path=refresh(ident+'-PRE',remaining)
            wait(start-55)
            cmd(['python3','-B',ROOT/'scripts/business_gate.py','--manifest',args.manifest,'--manifest-sha',args.manifest_sha,'--action-id',ident,'--phase','BASELINE'],80)
            baseline_path=CONTROL/(ident+'-baseline.json');baseline=read(baseline_path);health=read(health_path)
            check()
            proof=read(a['watchdog_reachability_file'])
            if sha(a['watchdog_reachability_file'])!=a['watchdog_reachability_sha256'] or proof['status']!='PINNED_CLEANUP_TIMER_REACHABILITY_PASS':raise RuntimeError('WATCHDOG_CURRENT_REACHABILITY')
            if baseline['status']!='BUSINESS_GATE_PASS' or not 0<=time.time()-stamp(health['at_utc'])<=90 or not 0<=time.time()-stamp(baseline['at_utc'])<=180:raise RuntimeError('AUTHORIZATION_PREREQUISITES')
            auth={'run_id':RUN_ID,'action_id':ident,'manifest_sha256':args.manifest_sha,'permission':'BOUND_QUALIFIED_FAULT','at_utc':now(),
                  'health_sha256':sha(health_path),'baseline_sha256':sha(baseline_path),'heartbeat_sha256':sha(CONTROL/'heartbeat.json'),'business_fault_permission':True,'status':'COMPETITION_PREFLIGHT_AUTHORIZED','faults_allowed':True,'actual_environment_pass':True,
                  'independent_watchdog_pass':True,'registration_sha256':a['qualified_registration_sha256'],
                  'watchdog_proof_file':a['watchdog_proof_file'],'watchdog_proof_sha256':a['watchdog_proof_sha256']}
            ap=CONTROL/(ident+'-authorization.json');atomic(ap,auth,True);copy(worker(a),ap,Path(a['runtime'])/'authorization.json')
            granted[ident]=sha(ap);event('AUTHORIZED',action=ident,permission='BOUND_QUALIFIED_FAULT')
            wait(start+a['duration_seconds']+13);check()
            if ident not in result['stages']:raise RuntimeError('NO_EXECUTION_FEEDBACK')
            granted.pop(ident,None);backend(a,'cancel');done.add(ident);event('REVOKED_AND_CLEANED',action=ident)
            copy(worker(a),a['runtime'],CONTROL/(ident+'-worker'),True,True)
            # A second arm must fail; it cannot quietly reset a cancelled task.
            try:backend(a,'arm')
            except RuntimeError as err:
                if 'CANNOT_REARM_CANCELLED_OR_STARTED' not in str(err):raise
                result['stages'][ident]['rearm_rejected']=True
            else:raise RuntimeError('CANCELLED_ACTION_REARMED')
            wait(stamp(p['start_utc'])+a['recovery_offset_seconds'])
            cmd(['python3','-B',ROOT/'scripts/business_gate.py','--manifest',args.manifest,'--manifest-sha',args.manifest_sha,'--action-id',ident,'--phase','RECOVERY'],80)
            cmd(['python3','-B',ROOT/'scripts/pilot_recovery.py','--layer',a['mechanism_id']],80)
            copy(worker(a),a['injection_output'],CONTROL/(ident+'-injection'),True,True)
            refresh(ident+'-RECOVERY',m['actions'][index+1:]);event('PUBLIC_AND_PRIVATE_RECOVERY_PASS',action=ident,actual_fault_recovery_tested=True)
            result['stages'][ident]['baseline']=str(baseline_path);result['stages'][ident]['public_recovery']=str(CONTROL/(ident+'-recovery.json'))
        while time.time()<stamp(p['start_utc'])+p['duration_seconds']+510:
            if check():break
            time.sleep(.5)
        else:raise RuntimeError('CAPTURE_COMPLETION_TIMEOUT')
        result['status']='FORMAL_EXECUTION_COMPLETE'
    except BaseException as e:result['error']=type(e).__name__+': '+str(e);event('ABORTING',error=result['error'])
    finally:
        granted.clear()
        for a in armed:
            try:
                backend(a,'cancel');result.setdefault('cancelled',[]).append(a['action_id'])
                dst=CONTROL/(a['action_id']+'-worker-final')
                copy(worker(a),a['runtime'],dst,True,True)
            except Exception as e:result.setdefault('cleanup_errors',[]).append(str(e));result['status']='FORMAL_EXECUTION_FAILED'
        if observer_started and result['status']!='FORMAL_EXECUTION_COMPLETE':subprocess.run(['systemctl','stop',m['observer_unit']+'.service'],capture_output=True,timeout=35)
        try:result['post_health']=str(refresh('POST'))
        except Exception as e:result['post_health_error']=str(e);result['status']='FORMAL_EXECUTION_FAILED'
        result['completed_utc']=now();atomic(CONTROL/'controller-terminal.json',result,True);print(json.dumps(result),flush=True)
    if result['status']!='FORMAL_EXECUTION_COMPLETE':raise SystemExit(1)
if __name__=='__main__':main()
