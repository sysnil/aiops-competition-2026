#!/usr/bin/env python3
"""Bounded Service/POD competition adapter with independent exact-PID recovery."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time

ROOT=Path('/root/ops-competition-2026')
RUNTIME=ROOT/'qualification/private-runtime'
CONTRACT=ROOT/'plans/competition-fault-admission.yaml'
HOLDER=ROOT/'scripts/resource_memory_holder.py'
IDS={'COMP-SF01-SERVICE-SHORT':('SERVICE',30),
     'COMP-SF01-SERVICE-300':('SERVICE',300),
     'COMP-SF01-POD-SHORT':('POD',60),
     'COMP-SF01-POD-300':('POD',300),
     'SF-PILOT-01-SERVICE':('SERVICE',300),
     'SF-PILOT-01-POD':('POD',300)}

def now():return datetime.now(timezone.utc).isoformat()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())

def save(p,value,replace=False):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not replace:
        with p.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
        p.chmod(0o600)
    else:
        temp=p.with_suffix('.tmp')
        with temp.open('w') as f:json.dump(value,f,indent=2);f.write('\n')
        temp.chmod(0o600);os.replace(temp,p)

def proc(pid):
    try:
        fields=Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()
        cg=Path('/proc',str(pid),'cgroup').read_text().strip().split('::',1)[1]
        return {'pid':pid,'start_ticks':fields[19],'process_state':fields[0],
                'cgroup':str(Path('/sys/fs/cgroup')/cg.lstrip('/'))}
    except FileNotFoundError:return None

def same(item):
    live=proc(item['pid'])
    return live is not None and live['start_ticks']==item['start_ticks'] and live['cgroup']==item['cgroup']

def same_child(item):
    live=proc(item['pid'])
    return live is not None and live['start_ticks']==item['start_ticks'] and live['cgroup'] in item.get('allowed_cgroups',[item['cgroup']])

def verify_target(t):
    live=proc(t['pid'])
    if not same(t):raise RuntimeError('Target PID/start-time/cgroup changed')
    cg=Path(t['cgroup'])
    if t['uid'].replace('-','_') not in str(cg) or t['container_id'].removeprefix('containerd://') not in str(cg):
        raise RuntimeError('Target UID/container does not identify exact cgroup')
    if live['process_state'] in ('T','t','Z'):raise RuntimeError('Target already stopped or exited')
    return live

def mem_available():
    return next(int(l.split()[1])*1024 for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:'))

def events(cg):
    return {k:int(v) for k,v in (l.split() for l in (Path(cg)/'memory.events').read_text().splitlines())}

def validate_registration(reg,health,registration_sha):
    if reg.get('id') not in IDS or (reg.get('layer'),reg.get('duration_seconds'))!=IDS[reg['id']]:
        raise RuntimeError('Unregistered competition ID/layer/duration')
    if reg.get('contract_sha256')!=sha(CONTRACT) or reg.get('adapter_sha256')!=sha(Path(__file__)):
        raise RuntimeError('Contract or adapter source lock changed')
    if health.get('status')!='COMPETITION_PREFLIGHT_AUTHORIZED' or health.get('faults_allowed') is not True or health.get('registration_sha256')!=registration_sha:
        raise RuntimeError('No competition-specific injection authorization')
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(health['at_utc'])).total_seconds()
    if not 0<=age<=180 or health.get('actual_environment_pass') is not True or health.get('independent_watchdog_pass') is not True:
        raise RuntimeError('Stale or incomplete live safety/watchdog gate')
    if reg.get('host')!=socket.gethostname() or reg['host'] not in ('k8s-worker2','k8s-worker3'):
        raise RuntimeError('Fault not on its registered Worker')
    expected=2 if reg['layer']=='SERVICE' else 1
    if len(reg.get('targets',[]))!=expected or len({t['uid'] for t in reg['targets']})!=expected:
        raise RuntimeError('Wrong unique target scope')
    if reg['layer']=='SERVICE' and any(t.get('service')!='recommendation' for t in reg['targets']):
        raise RuntimeError('Service candidate changed')
    if reg['layer']=='POD' and (reg.get('allocation_bytes')!=32*1024**2 or any(t.get('service')!='frontend' for t in reg['targets']) or reg.get('holder_sha256')!=sha(HOLDER)):
        raise RuntimeError('Pod mechanism strength or source changed')
    for target in reg['targets']:verify_target(target)
    if mem_available()<8*1024**3:raise RuntimeError('Worker lacks 8 GiB safety reserve')

def authorization_evidence(reg,health):
    proof_path=Path(health['watchdog_proof_file'])
    if not proof_path.is_relative_to(ROOT/'qualification') or sha(proof_path)!=health['watchdog_proof_sha256']:
        raise RuntimeError('Independent watchdog proof source invalid')
    proof=read(proof_path)
    if proof['status']!='COMPETITION_WATCHDOG_SELFTEST_PASS' or proof['adapter_sha256']!=sha(Path(__file__)):
        raise RuntimeError('Independent watchdog not tested with this adapter version')
    requirements=[]
    if reg['id'].endswith('-300'):
        requirements=['COMP-SF01-'+reg['layer']+'-SHORT']
    elif reg['id'].startswith('SF-PILOT-01-'):
        requirements=['COMP-SF01-SERVICE-300','COMP-SF01-POD-300']
    supplied={row['id']:row for row in reg.get('prerequisites',[])}
    for ident in requirements:
        if ident not in supplied:raise RuntimeError('Earlier qualification prerequisite absent')
        row=supplied[ident];path=Path(row['file'])
        if not path.is_relative_to(ROOT/'qualification') or sha(path)!=row['sha256']:
            raise RuntimeError('Qualification prerequisite hash/path invalid')
        decision=read(path)
        if decision.get('id')!=ident or decision.get('qualified') is not True:
            raise RuntimeError('Earlier mechanism/duration did not qualify')

def safe_state(path):
    path=Path(path)
    if path.parent!=RUNTIME or path.is_symlink() or not path.name.endswith('.json'):
        raise RuntimeError('State outside independent private runtime')
    return path

def cleanup(path):
    path=safe_state(path);state=read(path)
    if state.get('host')!=socket.gethostname() or state.get('layer') not in ('SERVICE','POD','SELFTEST'):
        raise RuntimeError('Cleanup scope/host invalid')
    if state.get('status')=='CLEANED':return state['cleanup_result']
    restored=[]
    for target in state.get('stopped_targets',[]):
        if not same(target):raise RuntimeError('Cannot prove paused target identity for recovery')
        os.kill(target['pid'],signal.SIGCONT)
        for _ in range(20):
            live=proc(target['pid'])
            if live and live['process_state'] not in ('T','t'):break
            time.sleep(.05)
        else:raise RuntimeError('Target remains stopped after SIGCONT')
        restored.append(target['pid'])
    terminated=[]
    for child in state.get('children',[]):
        live=proc(child['pid'])
        if live and live['start_ticks']==child['start_ticks'] and not same_child(child):
            raise RuntimeError('Owned child escaped both registered cgroups')
        if same_child(child):
            os.kill(child['pid'],signal.SIGCONT);os.kill(child['pid'],signal.SIGTERM)
            for _ in range(30):
                live=proc(child['pid'])
                if not same_child(child) or live['process_state']=='Z':break
                time.sleep(.1)
            else:
                if same_child(child):os.kill(child['pid'],signal.SIGKILL)
        terminated.append(child['pid'])
    result={'status':'COMPETITION_CLEANUP_ACTIONS_VERIFIED','at_utc':now(),
            'continued_pids':restored,'terminated_owned_child_pids':terminated}
    state['status']='CLEANED';state['cleanup_result']=result
    save(path,state,replace=True)
    return result

def arm(path,delay,unit):
    subprocess.run(['systemd-run','--quiet','--unit='+unit,'--on-active='+str(delay)+'s',
        '--timer-property=AccuracySec=100ms','--timer-property=RandomizedDelaySec=0',
        '--property=RuntimeMaxSec=30','/usr/bin/python3','-B',str(Path(__file__).resolve()),
        'cleanup','--state',str(path)],check=True,capture_output=True,text=True,timeout=15)
    result=subprocess.run(['systemctl','is-active',unit+'.timer'],capture_output=True,text=True,timeout=10)
    if result.returncode or result.stdout.strip()!='active':raise RuntimeError('Independent recovery timer not active')
    accuracy=subprocess.check_output(['systemctl','show',unit+'.timer','-p','AccuracyUSec','--value'],text=True,timeout=10).strip()
    if accuracy!='100ms':raise RuntimeError('Independent recovery timer accuracy not confirmed')

def stop_timer(unit):
    subprocess.run(['systemctl','stop',unit+'.timer'],check=True,capture_output=True,text=True,timeout=10)

def selftest(output):
    if output.exists():raise RuntimeError('Immutable watchdog test already exists')
    RUNTIME.mkdir(parents=True,exist_ok=True,mode=0o700)
    token='sf01-watchdog-v2-'+socket.gethostname()
    path=RUNTIME/(token+'.json')
    if path.exists():raise RuntimeError('Selftest state already exists')
    child=subprocess.Popen(['/usr/bin/sleep','20'])
    disposable_holder=subprocess.Popen(['/usr/bin/sleep','20'])
    item=proc(child.pid)
    state={'id':token,'host':socket.gethostname(),'layer':'SELFTEST','status':'ARMED',
           'stopped_targets':[item],'children':[proc(disposable_holder.pid)]}
    save(path,state)
    unit='ops-'+token
    result={'status':'WATCHDOG_SELFTEST_FAILED','business_faults_injected':0,'at_utc':now()}
    try:
        armed=time.monotonic()
        arm(path,3,unit);os.kill(child.pid,signal.SIGSTOP)
        time.sleep(5)
        after=read(path)
        if after['status']!='CLEANED' or not same(item) or proc(child.pid)['process_state'] in ('T','t'):
            raise RuntimeError('Independent timer did not resume exact sacrificial process')
        if disposable_holder.poll() is None:
            raise RuntimeError('Independent timer did not terminate owned sacrificial holder')
        result.update({'status':'COMPETITION_WATCHDOG_SELFTEST_PASS',
            'timer_unit':unit,'cleanup_result':after['cleanup_result'],
            'timer_accuracy':'100ms','configured_delay_seconds':3,
            'verification_elapsed_seconds':time.monotonic()-armed,
            'adapter_sha256':sha(Path(__file__))})
    finally:
        if same(item):os.kill(child.pid,signal.SIGCONT)
        if child.poll() is None:child.terminate()
        child.wait(timeout=10)
        if disposable_holder.poll() is None:disposable_holder.terminate()
        disposable_holder.wait(timeout=10)
        stop_timer(unit)
        save(output,result)
    return result

def run(registration,health_path,output):
    if output.exists():raise RuntimeError('Attempt output already exists')
    reg=read(registration);health=read(health_path)
    validate_registration(reg,health,sha(registration))
    authorization_evidence(reg,health)
    if output!=ROOT/'qualification/private-attempts'/reg['id'] or output.is_symlink():
        raise RuntimeError('Attempt output outside registered private directory')
    output.mkdir(parents=True,mode=0o700)
    path=RUNTIME/(reg['id']+'.json');safe_state(path)
    if path.exists():raise RuntimeError('Attempt state already exists')
    state={**reg,'status':'ARMED','registration_sha256':sha(registration),
        'stopped_targets':[],'children':[],'prepared_at_utc':now()}
    save(path,state)
    unit='ops-'+reg['id'].lower()+'-recovery'
    child=None;result={'status':'COMPETITION_FAULT_FAILED','id':reg['id'],'layer':reg['layer']}
    for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):
        signal.signal(sig,lambda signum,frame:(_ for _ in ()).throw(SystemExit(128+signum)))
    try:
        arm(path,reg['duration_seconds']+5,unit)
        state['watchdog_unit']=unit
        if reg['layer']=='SERVICE':
            state['stopped_targets']=[{k:t[k] for k in ('pid','start_ticks','cgroup')} for t in reg['targets']]
            save(path,state,replace=True)
            for t in state['stopped_targets']:
                if not same(t):raise RuntimeError('Target changed immediately before stop')
                os.kill(t['pid'],signal.SIGSTOP)
        else:
            target=reg['targets'][0];cg=Path(target['cgroup'])
            initial=int((cg/'memory.current').read_text());limit=int((cg/'memory.max').read_text())
            if initial+64*1024**2>limit*.8:raise RuntimeError('Insufficient conservative target memory headroom')
            state['memory_events_before']=events(cg)
            child=subprocess.Popen(['/usr/bin/python3','-B',str(HOLDER),'--bytes',str(reg['allocation_bytes'])],
                stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            info=proc(child.pid)
            info['allowed_cgroups']=[info['cgroup'],str(cg)]
            state['children']=[info];save(path,state,replace=True)
            (cg/'cgroup.procs').write_text(str(child.pid))
            current=proc(child.pid)
            if current['cgroup']!=str(cg):raise RuntimeError('Memory helper did not enter target container cgroup')
            current['allowed_cgroups']=info['allowed_cgroups']
            state['children']=[current];save(path,state,replace=True)
            child.stdin.write(b'1');child.stdin.flush();child.stdin.close()
        state['status']='ACTIVE';state['actual_start_utc']=now();save(path,state,replace=True)
        began=time.monotonic();result['actual_start_utc']=state['actual_start_utc']
        with (output/'private-samples.jsonl').open('x') as stream:
            while time.monotonic()-began<reg['duration_seconds']:
                sample={'at_utc':now(),'worker_memory_available':mem_available(),'targets':[]}
                if sample['worker_memory_available']<8*1024**3:raise RuntimeError('Worker memory safety reserve breached')
                for target in reg['targets']:
                    if not same(target):raise RuntimeError('Target PID/container identity changed during fault')
                    live=proc(target['pid']);cg=Path(target['cgroup'])
                    if reg['layer']=='SERVICE' and live['process_state'] not in ('T','t'):
                        raise RuntimeError('Service stall not active on all replicas')
                    mem=int((cg/'memory.current').read_text());limit=int((cg/'memory.max').read_text())
                    if mem>limit*.8:raise RuntimeError('Target exceeds 80 percent memory safety limit')
                    if reg['layer']=='POD' and (child.poll() is not None or any(
                        events(cg)[key]!=state['memory_events_before'][key] for key in ('oom','oom_kill'))):
                        raise RuntimeError('Allocator exited or an OOM event occurred')
                    sample['targets'].append({**live,'memory_current_bytes':mem,'memory_limit_bytes':limit})
                stream.write(json.dumps(sample)+'\n');stream.flush()
                time.sleep(min(1,max(0,reg['duration_seconds']-(time.monotonic()-began))))
        result['duration_seconds']=time.monotonic()-began
        result['status']='COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED'
    except BaseException as error:
        result['error']=type(error).__name__+': '+str(error)
    finally:
        try:
            result['cleanup']=cleanup(path)
            stop_timer(unit)
        except Exception as error:
            result['status']='COMPETITION_RECOVERY_FAILED_STOP_ALL'
            result['cleanup_error']=str(error)
        if child is not None:
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:pass
        result['actual_end_utc']=now()
        result['registration_sha256']=sha(registration)
        save(output/'injection-result.json',result)
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('selftest','run','cleanup'))
    parser.add_argument('--state',type=Path);parser.add_argument('--registration',type=Path)
    parser.add_argument('--health',type=Path);parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.action=='cleanup':value=cleanup(args.state)
    elif args.action=='selftest':value=selftest(args.output)
    else:value=run(args.registration,args.health,args.output)
    print(json.dumps(value),flush=True)
    if 'FAILED' in value['status']:raise SystemExit(1)

if __name__=='__main__':main()
