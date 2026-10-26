#!/usr/bin/env python3
"""One-case controller; no retries, no parameter changes, independent Worker cleanup."""
import json
from datetime import datetime,timezone
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from run_competition_qualification import (LOCAL,ROOT,remote,copy,optional,ssh)

RUN='/data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01'
UNIT='ops-case-sf-pilot-01'
PROD=ROOT+'/production-20261014'
TELEMETRY=ROOT+'/collector-telemetry-repair-20261013/results/production-telemetry-readiness-v2.json'

def refresh(suffix,active=False):
    ident='COMP-SF01-CASE-'+suffix
    command=[sys.executable,'-B',str(LOCAL/'scripts/refresh_case_safety.py'),'--id',ident]
    if active:command+=['--expected-observation-unit',UNIT+'.service']
    r=subprocess.run(command,capture_output=True,text=True,timeout=120)
    print(r.stdout.strip(),flush=True)
    if r.returncode:raise RuntimeError('Fresh real safety failed: '+r.stderr[-1000:])
    return '/data/stage5-v3/ops-competition-2026/audits/'+ident+'/phase-a-decision.json'

def main():
    telemetry=optional('master',TELEMETRY)
    if not telemetry or telemetry.get('status')!='COLLECTOR_PATH_READY_FOR_PILOT':
        raise RuntimeError('Production capture readiness blocked; no case attempt will be created')
    local=LOCAL/'runs/SF-PILOT-01'
    if local.exists():raise RuntimeError('One immutable case attempt only')
    local.mkdir(parents=True,mode=0o700)
    planpath=ROOT+'/plans/SF-PILOT-01.json';plan=json.loads(remote('master',['cat',planpath]))
    copy('master',planpath,local/'plan.json',True)
    if len(plan.get('prerequisites',[]))!=2:raise RuntimeError('Both mechanisms must qualify')
    result={'id':'SF-PILOT-01','status':'CASE_CONTROLLER_INVALID','faults':{},'contest_server_accessed':False}
    dispatched=[];observer_started=False
    def check():
        observed=optional('master',RUN+'/observation-result.json')
        if observed and observed['status']!='CASE_OBSERVATION_COMPLETE_PENDING_DATA_QUALITY':raise RuntimeError('Observer aborted: '+str(observed))
        for worker,ident,started in dispatched:
            injection=optional(worker,ROOT+'/qualification/private-attempts/'+ident+'/injection-result.json')
            if injection:
                result['faults'][ident]=injection
                if injection['status']!='COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED' or abs(injection.get('duration_seconds',0)-300)>2 or injection.get('cleanup',{}).get('status')!='COMPETITION_CLEANUP_ACTIONS_VERIFIED':raise RuntimeError('Fault or recovery failed: '+str(injection))
                if abs(datetime.fromisoformat(injection['actual_start_utc']).timestamp()-started)>2:raise RuntimeError('Fault missed registered onset tolerance')
            elif time.time()>started+320:raise RuntimeError('Fault terminal evidence overdue')
        return observed
    def wait(stamp):
        while time.time()<stamp:
            check();time.sleep(min(4,max(0,stamp-time.time())))
    try:
        for item in plan['faults']:
            worker='w3' if item['id'].endswith('SERVICE') else 'w2'
            target=local/(item['id']+'.json');copy('master',item['plan'],target,True);copy(worker,target,item['plan'])
            for proof in plan['prerequisites']:
                tmp=local/(proof['id']+'-proof.json')
                if not tmp.exists():copy('master',proof['file'],tmp,True)
                remote(worker,['mkdir','-p',str(Path(proof['file']).parent)]);copy(worker,tmp,proof['file'])
        health=refresh('PRE')
        remote('master',['systemd-run','--unit='+UNIT,'--property=Environment=KUBECONFIG=/etc/kubernetes/admin.conf',
            '--property=RuntimeMaxSec=4380','/usr/bin/python3','-B',PROD+'/scripts/pilot_observer.py',
            '--plan',planpath,'--health',health]);observer_started=True
        schedule=None
        for _ in range(15):
            schedule=optional('master',RUN+'/schedule.json')
            if schedule:break
            time.sleep(1)
        if not schedule:raise RuntimeError('No fixed case schedule')
        print(json.dumps({'stage':'CASE_HOUR_STARTED','schedule':{k:schedule[k] for k in ('start_utc','end_utc','private_fault_schedule')}}),flush=True)
        for fault in plan['faults']:
            ident=fault['id'];layer=ident.rsplit('-',1)[1];worker='w3' if layer=='SERVICE' else 'w2'
            node='k8s-worker3' if worker=='w3' else 'k8s-worker2'
            start=datetime.fromisoformat(schedule['private_fault_schedule'][ident]['start_utc']).timestamp()
            wait(start-55)
            if optional('master',RUN+'/observation-result.json'):raise RuntimeError('Observer ended before planned fault')
            print(remote('master',['env','KUBECONFIG=/etc/kubernetes/admin.conf','python3','-B',
                PROD+'/scripts/pilot_baseline.py','--plan',fault['plan']],timeout=80),flush=True)
            health=refresh(layer+'-PREFAULT',True)
            auth=ROOT+'/runs/SF-PILOT-01/'+layer.lower()+'-authorization.json'
            remote('master',['python3','-B',ROOT+'/scripts/authorize_competition.py','--plan',fault['plan'],
                '--health',health,'--baseline',RUN+'/baseline-prefix-'+layer.lower()+'-decision.json',
                '--watchdog',ROOT+'/qualification/watchdog-v3-'+node+'.json','--output',auth])
            copy('master',auth,local/(layer.lower()+'-authorization.json'),True)
            remote(worker,['mkdir','-p',str(Path(auth).parent)]);copy(worker,local/(layer.lower()+'-authorization.json'),auth)
            if time.time()>start-1:raise RuntimeError('Missed registered injection schedule')
            wait(start)
            remote(worker,['systemd-run','--unit=ops-inject-'+ident.lower(),'--property=RuntimeMaxSec=330',
                '/usr/bin/python3','-B',ROOT+'/scripts/competition_adapter.py','run','--registration',fault['plan'],
                '--health',auth,'--output',ROOT+'/qualification/private-attempts/'+ident])
            dispatched.append((worker,ident,start));print(json.dumps({'stage':'CASE_BOUNDED_FAULT_DISPATCHED','id':ident}),flush=True)
            wait(start+315);check()
            if ident not in result['faults']:raise RuntimeError('Missing injection/cleanup terminal')
            copy(worker,ROOT+'/qualification/private-attempts/'+ident,local/ident,True,True)
            remote('master',['mkdir','-p',RUN+'/private-injections'])
            copy('master',local/ident,RUN+'/private-injections/'+ident,recursive=True)
            print(json.dumps({'stage':'FAULT_CLEANUP_VERIFIED','id':ident}),flush=True)
            check_time=datetime.fromisoformat(schedule['start_utc']).timestamp()+plan['recovery_check_offsets_seconds'][layer]
            wait(check_time)
            print(remote('master',['python3','-B',PROD+'/scripts/pilot_recovery.py','--layer',layer],timeout=80),flush=True)
            refresh(layer+'-RECOVERY',True)
        deadline=datetime.fromisoformat(schedule['end_utc']).timestamp()+510
        while time.time()<deadline:
            if check():break
            time.sleep(4)
        else:raise RuntimeError('Full-hour capture terminal overdue')
        result['post_health']=refresh('POST')
        result['status']='CASE_EXECUTION_COMPLETE_PENDING_DATA_QUALITY'
    except BaseException as error:
        result['status']='CASE_CONTROLLER_INVALID'
        result['error']=type(error).__name__+': '+str(error)
        for worker,ident,_ in dispatched:
            subprocess.run(ssh(worker,['systemctl','stop','ops-inject-'+ident.lower()+'.service']),capture_output=True,timeout=35)
            try:result.setdefault('emergency_cleanup',{})[ident]=json.loads(remote(worker,['python3','-B',ROOT+'/scripts/competition_adapter.py','cleanup','--state',ROOT+'/qualification/private-runtime/'+ident+'.json']))
            except Exception as e:result.setdefault('cleanup_errors',{})[ident]=str(e)
        if observer_started:subprocess.run(ssh('master',['systemctl','stop',UNIT+'.service']),capture_output=True,timeout=35)
    finally:
        result['completed_utc']=datetime.now(timezone.utc).isoformat()
        with (local/'controller-terminal.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
        remote('master',['mkdir','-p',ROOT+'/runs/SF-PILOT-01']);copy('master',local/'controller-terminal.json',ROOT+'/runs/SF-PILOT-01/controller-terminal.json')
        print(json.dumps(result),flush=True)
    if result['status']!='CASE_EXECUTION_COMPLETE_PENDING_DATA_QUALITY':raise SystemExit(1)

if __name__=='__main__':main()
