#!/usr/bin/env python3
"""One independent qualification attempt. All recovery is also armed on the Worker."""

import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time

LOCAL=Path(__file__).resolve().parents[1]
ROOT='/root/ops-competition-2026'
DATA='/data/stage5-v3/ops-competition-2026/qualification'
HOSTS={'master':'192.168.53.52','w2':'192.168.53.54','w3':'192.168.53.55'}
ALLOWED={'COMP-SF01-SERVICE-SHORT','COMP-SF01-SERVICE-300','COMP-SF01-POD-SHORT','COMP-SF01-POD-300'}

def ssh(host,argv):return ['ssh','-o','BatchMode=yes','-S','/tmp/sfgen-'+host+'.sock','root@'+HOSTS[host],shlex.join(list(map(str,argv)))]
def remote(host,argv,timeout=100):
    r=subprocess.run(ssh(host,argv),capture_output=True,text=True,timeout=timeout)
    if r.returncode:raise RuntimeError(host+': '+r.stdout[-1000:]+' '+r.stderr[-1500:])
    return r.stdout
def optional(host,path):
    return json.loads(remote(host,['python3','-c','import pathlib,json; p=pathlib.Path('+repr(path)+'); print(p.read_text() if p.is_file() else "null")']))
def copy(host,source,destination,from_remote=False,recursive=False):
    base=['scp','-p','-o','BatchMode=yes','-o','ControlPath=/tmp/sfgen-'+host+'.sock']
    if recursive:base.append('-r')
    base += ['root@'+HOSTS[host]+':'+str(source),str(destination)] if from_remote else [str(source),'root@'+HOSTS[host]+':'+str(destination)]
    subprocess.run(base,check=True,capture_output=True,text=True,timeout=90)
def refresh(ident,unit=None):
    cmd=[sys.executable,'-B',str(LOCAL/'scripts/refresh_competition_safety.py'),'--id',ident]
    if unit:cmd+=['--expected-observation-unit',unit]
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=120)
    print(r.stdout.strip(),flush=True)
    if r.returncode:raise RuntimeError('Fresh competition safety failed: '+r.stderr[-700:])
    return '/data/stage5-v3/ops-competition-2026/audits/'+ident+'/phase-a-decision.json'
def wait_until(stamp):
    while time.time()<stamp:time.sleep(min(1,stamp-time.time()))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--id',choices=sorted(ALLOWED),required=True);args=parser.parse_args()
    ident=args.id;local=LOCAL/'qualification'/ident
    if local.exists():raise RuntimeError('Attempt controller record already exists')
    local.mkdir(parents=True,mode=0o700)
    worker='w3' if '-SERVICE-' in ident else 'w2'
    node='k8s-worker3' if worker=='w3' else 'k8s-worker2'
    planpath=ROOT+'/plans/'+ident+'.json'
    plan=json.loads(remote('master',['cat',planpath]))
    copy('master',planpath,local/'plan.json',True)
    copy(worker,local/'plan.json',planpath)
    for row in plan.get('prerequisites',[]):
        name=Path(row['file']).name
        target=local/(row['id']+'-prerequisite.json');copy('master',row['file'],target,True)
        remote(worker,['mkdir','-p',str(Path(row['file']).parent)])
        copy(worker,target,row['file'])
    obsunit='ops-observation-'+ident.lower()
    injectunit='ops-inject-'+ident.lower()
    run=DATA+'/'+ident
    observer_started=False;injection_started=False;injection=None
    result={'id':ident,'status':'QUALIFICATION_CONTROLLER_FAILED','fault_started':False,'formal_case':False}
    try:
        prehealth=refresh(ident+'-PRE')
        remote('master',['systemd-run','--unit='+obsunit,'--property=Environment=KUBECONFIG=/etc/kubernetes/admin.conf',
            '--property=RuntimeMaxSec='+str(sum(plan['durations_seconds'])+450),'/usr/bin/python3','-B',
            ROOT+'/scripts/competition_observation.py','--plan',planpath,'--health',prehealth])
        observer_started=True
        schedule=None
        for _ in range(15):
            schedule=optional('master',run+'/schedule.json')
            if schedule:break
            time.sleep(1)
        if not schedule:raise RuntimeError('Observation did not create a schedule')
        fault_time=datetime.fromisoformat(schedule['windows']['during']['start_utc']).timestamp()
        end=datetime.fromisoformat(schedule['end_utc']).timestamp()
        print(json.dumps({'stage':'QUALIFICATION_OBSERVATION_RUNNING','id':ident,'schedule':schedule['windows']}),flush=True)
        wait_until(fault_time-55)
        if optional('master',run+'/observation-result.json'):raise RuntimeError('Observation ended before injection precheck')
        prefix=remote('master',['env','KUBECONFIG=/etc/kubernetes/admin.conf','python3','-B',
            ROOT+'/scripts/competition_baseline_prefix.py','--plan',planpath],timeout=80)
        print(prefix.strip(),flush=True)
        health=refresh(ident+'-PREFAULT',obsunit+'.service')
        auth=ROOT+'/qualification/'+ident+'/authorization.json'
        watchdog=ROOT+'/qualification/watchdog-v3-'+node+'.json'
        remote('master',['python3','-B',ROOT+'/scripts/authorize_competition.py','--plan',planpath,
            '--health',health,'--baseline',run+'/baseline-prefix-decision.json','--watchdog',watchdog,'--output',auth])
        copy('master',auth,local/'authorization.json',True)
        remote(worker,['mkdir','-p',ROOT+'/qualification/'+ident])
        copy(worker,local/'authorization.json',auth)
        if time.time()>fault_time-1:raise RuntimeError('Preflight missed the fixed injection schedule; no late injection')
        wait_until(fault_time)
        remote(worker,['systemd-run','--unit='+injectunit,'--property=RuntimeMaxSec='+str(plan['duration_seconds']+30),
            '/usr/bin/python3','-B',ROOT+'/scripts/competition_adapter.py','run','--registration',planpath,
            '--health',auth,'--output',ROOT+'/qualification/private-attempts/'+ident])
        injection_started=True;result['fault_unit_dispatched']=True
        print(json.dumps({'stage':'BOUNDED_FAULT_UNIT_DISPATCHED','id':ident}),flush=True)
        while time.time()<end+330:
            observed=optional('master',run+'/observation-result.json')
            injection=optional(worker,ROOT+'/qualification/private-attempts/'+ident+'/injection-result.json')
            state=optional(worker,ROOT+'/qualification/private-runtime/'+ident+'.json')
            result['fault_started']=bool(state and state.get('actual_start_utc'))
            if injection and injection['status']!='COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED':
                raise RuntimeError('Fault failed or recovery uncertain: '+json.dumps(injection))
            if observed:
                if observed['status']!='OBSERVATION_COMPLETE_NOT_FAULT_QUALIFICATION':raise RuntimeError('Observation aborted: '+str(observed))
                break
            time.sleep(4)
        else:raise RuntimeError('Observation completion deadline exceeded')
        if not injection:raise RuntimeError('No verified injection/recovery terminal')
        copy(worker,ROOT+'/qualification/private-attempts/'+ident,local/'private-injection',True,True)
        copy('master',local/'private-injection',run+'/private-injection',recursive=True)
        posthealth=refresh(ident+'-POST')
        decision=ROOT+'/qualification/'+ident+'/decision.json'
        outcome=remote('master',['python3','-B',ROOT+'/scripts/competition_qualification.py','--plan',planpath,
            '--injection-result',run+'/private-injection/injection-result.json','--post-health',posthealth,'--output',decision],timeout=180)
        print(outcome.strip(),flush=True)
        result['status']='QUALIFICATION_CONTROLLER_COMPLETED'
        result['decision']=optional('master',decision)
    except BaseException as error:
        result['error']=type(error).__name__+': '+str(error)
        if injection_started:
            subprocess.run(ssh(worker,['systemctl','stop',injectunit+'.service']),capture_output=True,timeout=35)
            try:result['emergency_cleanup']=json.loads(remote(worker,['python3','-B',ROOT+'/scripts/competition_adapter.py','cleanup',
                '--state',ROOT+'/qualification/private-runtime/'+ident+'.json']))
            except Exception as cleanup_error:result['cleanup_error']=str(cleanup_error)
        if observer_started:
            subprocess.run(ssh('master',['systemctl','stop',obsunit+'.service']),capture_output=True,timeout=35)
    finally:
        if injection_started:
            try:
                state=optional(worker,ROOT+'/qualification/private-runtime/'+ident+'.json')
                result['fault_started']=bool(state and state.get('actual_start_utc'))
            except Exception as error:result['actual_injection_evidence_status']='UNKNOWN: '+str(error)
        result['completed_utc']=datetime.now(timezone.utc).isoformat()
        with (local/'controller-terminal.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
        remote('master',['mkdir','-p',ROOT+'/qualification/'+ident])
        copy('master',local/'controller-terminal.json',ROOT+'/qualification/'+ident+'/controller-terminal.json')
        print(json.dumps({'status':result['status'],'id':ident,'fault_started':result['fault_started'],'error':result.get('error')}),flush=True)
    if result['status']!='QUALIFICATION_CONTROLLER_COMPLETED':raise SystemExit(1)

if __name__=='__main__':main()
