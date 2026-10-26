#!/usr/bin/env python3
"""Master-only controller; Workers own exact UTC dispatch and immediate fail-closed rollback."""
import json,subprocess,time,signal
from datetime import datetime,timezone
from pathlib import Path
from master_transport import remote,copy,optional,clock,lease
from local_scheduler import sha,save
from pilot_common import OPS,PROD,RUN,CONTROL,RUN_ID,read,stamp,now
from handshake import Gate,atomic,digest
import argparse,os

UNIT='ops-case-sf-pilot-01-attempt-03'
IDENT='SF-PILOT-01-ATTEMPT-03'
def command(args,timeout=120):
    r=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=timeout)
    if r.returncode:raise RuntimeError(r.stdout[-1500:]+r.stderr[-1500:])
    return r.stdout
def refresh(suffix,active=False):
    ident='COMP-SF01-A03-'+suffix
    args=['python3','-B',PROD/'scripts/master_refresh_safety.py','--id',ident]
    if active:args+=['--expected-observation-unit',UNIT+'.service']
    print(command(args),flush=True)
    return Path('/data/stage5-v3/ops-competition-2026/audits')/ident/'phase-a-decision.json'

def main():
    if __import__('socket').gethostname()!='k8s-master':raise RuntimeError('Controller must execute on Master')
    global UNIT,IDENT
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,required=True);args=parser.parse_args()
    planpath=args.plan;plan=read(planpath);IDENT=RUN_ID;UNIT=plan['observer_unit']
    if plan['id']!=RUN_ID:raise RuntimeError('RUN_CONTEXT_MISMATCH')
    formal=plan['mode']=='FORMAL'
    proof=PROD/'results/joint-rehearsal-verdict.json'
    if formal and (not proof.exists() or read(proof)['status']!='JOINT_REHEARSAL_PASS'):raise RuntimeError('Real joint rehearsal not verified')
    if (CONTROL/'controller-started.json').exists() or RUN.exists():raise RuntimeError('Exactly one new formal attempt')
    save(CONTROL/'controller-started.json',{'at_utc':now(),'plan_sha256':sha(planpath)})
    result={'id':IDENT,'status':'CASE_CONTROLLER_INVALID','faults':{},'onsets':{},'contest_servers_accessed':False}
    tasks=[];authorized=set();observing=False;last_clock=0;clocks={};gate=None;last_gate_state=None;exit_test_fired=False
    def observer_alive():
        return int(subprocess.check_output(['systemctl','show',UNIT+'.service','-p','MainPID','--value'],text=True).strip() or 0)==observer_pid
    for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):
        signal.signal(sig,lambda n,f:(_ for _ in ()).throw(SystemExit(128+n)))
    def check():
        nonlocal clocks,last_clock
        ob=read(RUN/'observation-result.json') if (RUN/'observation-result.json').exists() else None
        if ob and ob['status']!='CASE_OBSERVATION_COMPLETE_PENDING_DATA_QUALITY':raise RuntimeError('OBSERVER_ABORT: '+str(ob))
        nonlocal last_gate_state,exit_test_fired
        if ob and ob['status']=='CASE_OBSERVATION_COMPLETE_PENDING_DATA_QUALITY':return ob
        hb=read(CONTROL/'heartbeat.json') if (CONTROL/'heartbeat.json').exists() else None
        gate.accept(hb,time.time(),observer_pid,observer_alive())
        if gate.state!=last_gate_state:
            with (CONTROL/'controller-states.jsonl').open('a') as f:f.write(json.dumps({'state':gate.state,'at_utc':now(),'sequence':gate.sequence})+'\n')
            last_gate_state=gate.state
        if gate.state not in ('READY','RUNNING'):raise RuntimeError('NOT_READY_NO_LEASE')
        if any((RUN/'controls'/(x+'-failure.json')).exists() for x in ('image_asset','cart_view')):raise RuntimeError('CONTROL_FAILURE')
        if plan['mode']=='EXIT_TEST' and not exit_test_fired and time.time()>=stamp(schedule['start_utc'])+35:
            exit_test_fired=True
            save(CONTROL/'observer-exit-test-trigger.json',{'at_utc':now(),'faults_injected':0,'signal':'SIGTERM','expected_outcome':'controller abort and cancel all future marker timers'})
            subprocess.run(['systemctl','stop',UNIT+'.service'],check=True,timeout=30)
            raise RuntimeError('EXPECTED_OBSERVER_EXIT_TEST')
        if time.monotonic()-last_clock>15:
            clocks={h:clock(h,3) for h in ('w2','w3')};last_clock=time.monotonic()
            with (CONTROL/'clock-runtime.jsonl').open('a') as f:f.write(json.dumps(clocks)+'\n')
            if any(abs(v['worker_minus_master_seconds'])+v['uncertainty_seconds']>.5 for v in clocks.values()):raise RuntimeError('CLOCK_BOUND_FAILED')
        for task in tasks:
            p,h=task['plan'],task['host'];ident=p['id'];start=stamp(p['planned_utc'])
            actual=optional(h,Path(p['runtime'])/'actual-start.json')
            terminal=optional(h,Path(p['runtime'])/'terminal.json')
            if actual and ident not in result['onsets']:
                actual['controller_seen_master_utc']=now()
                actual['feedback_upper_seconds']=time.time()-stamp(actual['actual_worker_utc'])+clocks[h]['worker_minus_master_seconds']+clocks[h]['uncertainty_seconds']
                result['onsets'][ident]=actual
                save(CONTROL/(ident+'-immediate-onset.json'),actual)
                print(json.dumps({'stage':'IMMEDIATE_ONSET_CHECK','id':ident,'onset':actual}),flush=True)
                if not actual['pass'] or actual['feedback_upper_seconds']>2:raise RuntimeError('ONSET_OR_FEEDBACK_DEADLINE_FAILED')
            if start+2.5<time.time() and not actual:raise RuntimeError('ACTUAL_START_FEEDBACK_ABSENT')
            if terminal:
                if terminal['status']!='SCHEDULED_ACTION_COMPLETE':raise RuntimeError('WORKER_FAIL_CLOSED: '+str(terminal))
                if p['mode']=='MARKER':
                    result.setdefault('markers',{})[ident]=terminal
                    continue
                inj=terminal.get('injection',{})
                if abs(inj.get('duration_seconds',0)-300)>2 or inj.get('cleanup',{}).get('status')!='COMPETITION_CLEANUP_ACTIONS_VERIFIED':raise RuntimeError('FAULT_OR_CLEANUP_NOT_VERIFIED')
                result['faults'][ident]=inj
            elif time.time()>start+320:raise RuntimeError('INJECTION_TERMINAL_OVERDUE')
            elif ident in authorized and (p['mode']=='FAULT' or start-15<=time.time()<=start+5):lease(h,p,task['sha256'],clocks[h])
        return ob
    def wait(until):
        while time.time()<until:
            check();time.sleep(min(.3,max(0,until-time.time())))
    try:
        for f in plan['faults']:
            h='w3' if f['id'].endswith('SERVICE') else 'w2'
            copy(h,f['plan'],f['plan'])
            for proof in plan['prerequisites']:
                remote(h,['mkdir','-p',Path(proof['file']).parent]);copy(h,proof['file'],proof['file'])
        health=Path(plan['health_file'])
        if not 0<=time.time()-stamp(read(health)['at_utc'])<=180:raise RuntimeError('PRESTART_SAFETY_EXPIRED')
        launched_at=time.time()
        command(['systemd-run','--unit='+UNIT,'--property=Environment=KUBECONFIG=/etc/kubernetes/admin.conf',
                 '--property=Environment=SF_RUN_ID='+RUN_ID,'--property=RuntimeMaxSec=4380','/usr/bin/python3','-B',PROD/'scripts/pilot_observer.py','--plan',planpath,'--health',health]);observing=True
        while not (CONTROL/'initializing.json').exists():
            if time.time()-launched_at>=15:raise RuntimeError('OBSERVER_INITIALIZATION_DEADLINE')
            time.sleep(.05)
        initialization=read(CONTROL/'initializing.json');observer_pid=initialization['pid']
        gate=Gate(RUN_ID,plan['run_token'],sha(planpath),plan['source_sha256'][str(PROD/'scripts/pilot_observer.py')],launched_at,stamp(plan['start_utc']))
        while True:
            hb=read(CONTROL/'heartbeat.json') if (CONTROL/'heartbeat.json').exists() else None
            if gate.accept(hb,time.time(),observer_pid,observer_alive()):break
            time.sleep(.05)
        while not (RUN/'schedule.json').exists():
            if time.time()-launched_at>=15:raise RuntimeError('SCHEDULE_INITIALIZATION_DEADLINE')
            time.sleep(.05)
        schedule=read(RUN/'schedule.json');locks={}
        atomic(CONTROL/'ready-receipt.json',{'run_id':RUN_ID,'run_token':plan['run_token'],'plan_sha256':sha(planpath),'heartbeat_sha256':digest(hb),'sequence':hb['sequence'],'at_utc':now(),'first_check_seconds':stamp(hb['completed_utc'])-stamp(hb['check_started_utc']),'startup_seconds':time.time()-launched_at},True)
        result['handshake']=read(CONTROL/'ready-receipt.json')
        actions=plan['faults'] if formal else plan['markers']
        for f in actions:
            layer=f['id'].rsplit('-',1)[1];h=('w3' if layer=='SERVICE' else 'w2') if formal else f['host']
            p={'id':f['id'],'mode':'FAULT' if formal else 'MARKER','worker':'k8s-worker'+h[-1],'unit':'ops-a03-'+RUN_ID.lower()+'-'+layer.lower(),
               'runtime':str(PROD/'runtime'/f['id']),'tolerance_seconds':2,
               'planned_utc':schedule['private_fault_schedule'][f['id']]['start_utc'] if formal else datetime.fromtimestamp(stamp(plan['start_utc'])+f['offset_seconds'],timezone.utc).isoformat(),
               'scheduler_sha256':sha(PROD/'scripts/local_scheduler.py'),
               'registration':f.get('plan'),'registration_sha256':f.get('sha256'),
               'authorization':str(PROD/'runtime'/f['id']/'authorization.json'),
               'output':str(OPS/'qualification/private-attempts'/f['id'])}
            file=PROD/'plans'/('scheduled-'+RUN_ID+'-'+layer.lower()+'.json');save(file,p);plan_digest=sha(file);locks[str(file)]=plan_digest
            copy(h,file,file)
            task={'host':h,'plan':p,'file':str(file),'sha256':plan_digest};tasks.append(task)
            armed=remote(h,['python3','-B',PROD/'scripts/local_scheduler.py','arm','--plan',file,'--sha256',plan_digest])
            save(CONTROL/('timer-'+layer.lower()+'.json'),{'at_utc':now(),'plan_sha256':plan_digest,'systemd':armed})
        save(PROD/'plans'/('schedule-lock-'+RUN_ID+'.json'),locks)
        print(json.dumps({'stage':'READY_AND_TIMERS_ARMED','run_id':RUN_ID,'start_utc':schedule['start_utc'],'end_utc':schedule['end_utc']}),flush=True)
        if time.time()>=stamp(schedule['start_utc'])-5:raise RuntimeError('TIMERS_NOT_ARMED_BEFORE_T0')
        if not formal:authorized.update(t['plan']['id'] for t in tasks)
        for f,task in zip(plan['faults'],tasks):
            ident=f['id'];layer=ident.rsplit('-',1)[1];h=task['host'];start=stamp(task['plan']['planned_utc'])
            wait(start-105)
            health=refresh(layer+'-PREFAULT',True)
            wait(start-55)
            print(command(['env','KUBECONFIG=/etc/kubernetes/admin.conf','python3','-B',PROD/'scripts/pilot_baseline.py','--plan',f['plan']],80),flush=True)
            auth=Path(task['plan']['authorization'])
            command(['python3','-B',OPS/'scripts/authorize_competition.py','--plan',f['plan'],'--health',health,
                     '--baseline',RUN/('baseline-prefix-'+layer.lower()+'-decision.json'),
                     '--watchdog',OPS/'qualification'/('watchdog-v3-k8s-worker'+h[-1]+'.json'),'--output',auth])
            remote(h,['mkdir','-p',auth.parent]);copy(h,auth,auth)
            if time.time()>start-3:raise RuntimeError('MISSED_PRESTART_AUTHORIZATION_DEADLINE')
            authorized.add(ident)
            wait(start+315);check()
            if ident not in result['faults']:raise RuntimeError('MISSING_INJECTION_TERMINAL')
            (RUN/'private-injections').mkdir(exist_ok=True,mode=0o700)
            copy(h,task['plan']['output'],RUN/'private-injections'/ident,True,True)
            copy(h,task['plan']['runtime'],CONTROL/(ident+'-worker'),True,True)
            wait(stamp(schedule['start_utc'])+plan['recovery_check_offsets_seconds'][layer])
            print(command(['python3','-B',PROD/'scripts/pilot_recovery.py','--layer',layer],80),flush=True)
            refresh(layer+'-RECOVERY',True)
            print(json.dumps({'stage':'FAULT_AND_RECOVERY_PASS','id':ident}),flush=True)
        deadline=stamp(schedule['end_utc'])+510
        while time.time()<deadline:
            if check():break
            time.sleep(.5)
        else:raise RuntimeError('OBSERVATION_COMPLETION_OVERDUE')
        result['status']='CASE_EXECUTION_COMPLETE_PENDING_DATA_QUALITY'
    except BaseException as e:result['error']=type(e).__name__+': '+str(e)
    finally:
        # Revoke and cancel BOTH timers even if one fault was never authorized.
        for t in tasks:
            try:
                remote(t['host'],['python3','-B',PROD/'scripts/local_scheduler.py','cancel','--plan',t['file'],'--sha256',t['sha256']])
                result.setdefault('cancelled',[]).append(t['plan']['id'])
            except Exception as e:result.setdefault('cleanup_errors',[]).append(str(e));result['status']='CASE_CONTROLLER_INVALID'
        if result['status']!='CASE_EXECUTION_COMPLETE_PENDING_DATA_QUALITY' and observing:
            subprocess.run(['systemctl','stop',UNIT+'.service'],capture_output=True,timeout=35)
        try:result['post_health']=str(refresh(RUN_ID+'-POST'))
        except Exception as e:result['post_health_error']=str(e);result['status']='CASE_CONTROLLER_INVALID'
        result['completed_utc']=now()
        result['state']='FINISHED' if result['status']=='CASE_EXECUTION_COMPLETE_PENDING_DATA_QUALITY' else 'ABORTING'
        if plan['mode']=='EXIT_TEST':result['expected_exit_test_observed']=exit_test_fired and result.get('error','').endswith('EXPECTED_OBSERVER_EXIT_TEST') and len(result.get('cancelled',[]))==2 and not result.get('cleanup_errors')
        save(CONTROL/'controller-terminal.json',result);print(json.dumps(result),flush=True)
    if result['status']!='CASE_EXECUTION_COMPLETE_PENDING_DATA_QUALITY':raise SystemExit(1)
if __name__=='__main__':main()
