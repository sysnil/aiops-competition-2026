#!/usr/bin/env python3
"""Master-only controller; Workers own exact UTC dispatch and immediate fail-closed rollback."""
import json,subprocess,time,signal
from datetime import datetime,timezone
from pathlib import Path
from master_transport import remote,copy,optional,clock,lease
from local_scheduler import sha,save
from pilot_common import OPS,PROD,RUN,read,stamp,now

UNIT='ops-case-sf-pilot-01-attempt-02'
IDENT='SF-PILOT-01-ATTEMPT-02'
def command(args,timeout=120):
    r=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=timeout)
    if r.returncode:raise RuntimeError(r.stdout[-1500:]+r.stderr[-1500:])
    return r.stdout
def refresh(suffix,active=False):
    ident='COMP-SF01-A02-'+suffix
    args=['python3','-B',PROD/'scripts/master_refresh_safety.py','--id',ident]
    if active:args+=['--expected-observation-unit',UNIT+'.service']
    print(command(args),flush=True)
    return Path('/data/stage5-v3/ops-competition-2026/audits')/ident/'phase-a-decision.json'

def main():
    if __import__('socket').gethostname()!='k8s-master':raise RuntimeError('Controller must execute on Master')
    planpath=OPS/'plans'/(IDENT+'.json');plan=read(planpath)
    if read(PROD/'results/scheduler-rehearsal.json')['status']!='NO_FAULT_SCHEDULER_PASS':raise RuntimeError('Scheduler tests not PASS')
    if (PROD/'results/controller-started.json').exists() or RUN.exists():raise RuntimeError('Exactly one new formal attempt')
    save(PROD/'results/controller-started.json',{'at_utc':now(),'plan_sha256':sha(planpath)})
    result={'id':IDENT,'status':'CASE_CONTROLLER_INVALID','faults':{},'onsets':{},'contest_servers_accessed':False}
    tasks=[];authorized=set();observing=False;last_clock=0;clocks={}
    for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):
        signal.signal(sig,lambda n,f:(_ for _ in ()).throw(SystemExit(128+n)))
    def check():
        nonlocal clocks,last_clock
        ob=read(RUN/'observation-result.json') if (RUN/'observation-result.json').exists() else None
        if ob and ob['status']!='CASE_OBSERVATION_COMPLETE_PENDING_DATA_QUALITY':raise RuntimeError('OBSERVER_ABORT: '+str(ob))
        if time.time()>=stamp(schedule['start_utc']):
            rows=(RUN/'safety.jsonl').read_bytes().splitlines()
            if not rows or time.time()-stamp(json.loads(rows[-1])['at_utc'])>30:raise RuntimeError('LIVE_SAFETY_HEARTBEAT_STALE')
            if any((RUN/'controls'/(x+'-failure.json')).exists() for x in ('image_asset','cart_view')):raise RuntimeError('CONTROL_FAILURE')
        if time.monotonic()-last_clock>15:
            clocks={h:clock(h,3) for h in ('w2','w3')};last_clock=time.monotonic()
            with (PROD/'results/clock-runtime.jsonl').open('a') as f:f.write(json.dumps(clocks)+'\n')
            if any(abs(v['worker_minus_master_seconds'])+v['uncertainty_seconds']>.5 for v in clocks.values()):raise RuntimeError('CLOCK_BOUND_FAILED')
        for task in tasks:
            p,h=task['plan'],task['host'];ident=p['id'];start=stamp(p['planned_utc'])
            actual=optional(h,Path(p['runtime'])/'actual-start.json')
            terminal=optional(h,Path(p['runtime'])/'terminal.json')
            if actual and ident not in result['onsets']:
                actual['controller_seen_master_utc']=now()
                actual['feedback_upper_seconds']=time.time()-stamp(actual['actual_worker_utc'])+clocks[h]['worker_minus_master_seconds']+clocks[h]['uncertainty_seconds']
                result['onsets'][ident]=actual
                save(PROD/'results'/(ident+'-immediate-onset.json'),actual)
                print(json.dumps({'stage':'IMMEDIATE_ONSET_CHECK','id':ident,'onset':actual}),flush=True)
                if not actual['pass'] or actual['feedback_upper_seconds']>2:raise RuntimeError('ONSET_OR_FEEDBACK_DEADLINE_FAILED')
            if start+2.5<time.time() and not actual:raise RuntimeError('ACTUAL_START_FEEDBACK_ABSENT')
            if terminal:
                if terminal['status']!='SCHEDULED_ACTION_COMPLETE':raise RuntimeError('WORKER_FAIL_CLOSED: '+str(terminal))
                inj=terminal.get('injection',{})
                if abs(inj.get('duration_seconds',0)-300)>2 or inj.get('cleanup',{}).get('status')!='COMPETITION_CLEANUP_ACTIONS_VERIFIED':raise RuntimeError('FAULT_OR_CLEANUP_NOT_VERIFIED')
                result['faults'][ident]=inj
            elif time.time()>start+320:raise RuntimeError('INJECTION_TERMINAL_OVERDUE')
            elif ident in authorized:lease(h,p,task['sha256'],clocks[h])
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
        health=refresh('PRE')
        command(['systemd-run','--unit='+UNIT,'--property=Environment=KUBECONFIG=/etc/kubernetes/admin.conf',
                 '--property=RuntimeMaxSec=4380','/usr/bin/python3','-B',PROD/'scripts/pilot_observer.py','--plan',planpath,'--health',health]);observing=True
        for _ in range(20):
            if (RUN/'schedule.json').exists():break
            time.sleep(.5)
        schedule=read(RUN/'schedule.json');locks={}
        for f in plan['faults']:
            layer=f['id'].rsplit('-',1)[1];h='w3' if layer=='SERVICE' else 'w2'
            p={'id':f['id'],'mode':'FAULT','worker':'k8s-worker'+h[-1],'unit':'ops-a02-'+layer.lower(),
               'runtime':str(PROD/'runtime'/f['id']),'tolerance_seconds':2,
               'planned_utc':schedule['private_fault_schedule'][f['id']]['start_utc'],
               'scheduler_sha256':sha(PROD/'scripts/local_scheduler.py'),
               'registration':f['plan'],'registration_sha256':f['sha256'],
               'authorization':str(PROD/'runtime'/f['id']/'authorization.json'),
               'output':str(OPS/'qualification/private-attempts'/f['id'])}
            file=PROD/'plans'/('scheduled-'+layer.lower()+'.json');save(file,p);digest=sha(file);locks[str(file)]=digest
            copy(h,file,file)
            task={'host':h,'plan':p,'file':str(file),'sha256':digest};tasks.append(task)
            armed=remote(h,['python3','-B',PROD/'scripts/local_scheduler.py','arm','--plan',file,'--sha256',digest])
            save(PROD/'results'/('timer-'+layer.lower()+'.json'),{'at_utc':now(),'plan_sha256':digest,'systemd':armed})
        save(PROD/'plans/schedule-lock.json',locks)
        print(json.dumps({'stage':'MASTER_HOUR_AND_WORKER_TIMERS_ARMED','schedule':schedule}),flush=True)
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
            copy(h,task['plan']['runtime'],PROD/'results'/(ident+'-worker'),True,True)
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
        try:result['post_health']=str(refresh('POST'))
        except Exception as e:result['post_health_error']=str(e);result['status']='CASE_CONTROLLER_INVALID'
        result['completed_utc']=now();save(PROD/'results/controller-terminal.json',result);print(json.dumps(result),flush=True)
    if result['status']!='CASE_EXECUTION_COMPLETE_PENDING_DATA_QUALITY':raise SystemExit(1)
if __name__=='__main__':main()
