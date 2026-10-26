#!/usr/bin/env python3
"""Hash-bound Worker UTC trigger. The qualified adapter/recovery source stays unchanged.

Master supplies short-lived leases; no lease/revocation/late onset => fail closed.
Marker mode uses exactly the same timer, validation, lease and onset checks.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

BASE = Path('/root/ops-competition-2026/attempt02-20261014')
ADAPTER = Path('/root/ops-competition-2026/scripts/competition_adapter.py')
ADAPTER_HASH = '791481b3e12ce6f64bb77c75acaff7df8000d800081589194ccde5d5ad3f2a92'
FAULT_IDS = {'SF-PILOT-01-ATTEMPT-02-SERVICE': ('SERVICE', 300),
             'SF-PILOT-01-ATTEMPT-02-POD': ('POD', 300)}

def now(): return datetime.now(timezone.utc).isoformat()
def stamp(s): return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p): return json.loads(Path(p).read_text())
def save(p, v, replace=False):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if replace:
        temp=p.with_suffix('.tmp');temp.write_text(json.dumps(v,indent=2)+'\n');temp.chmod(0o600);os.replace(temp,p)
    else:
        with p.open('x') as f: json.dump(v,f,indent=2);f.write('\n')
        p.chmod(0o600)

def validate_lease(plan, digest, lease, at, revoked=False):
    if revoked: raise RuntimeError('AUTHORIZATION_REVOKED')
    if lease.get('plan_sha256') != digest or lease.get('authorized') is not True:
        raise RuntimeError('NO_HASH_BOUND_AUTHORIZATION')
    if not stamp(lease['issued_utc'])-0.5 <= at <= stamp(lease['expires_utc']):
        raise RuntimeError('AUTHORIZATION_LEASE_EXPIRED')
    if stamp(lease['expires_utc'])-stamp(lease['issued_utc']) > 5:
        raise RuntimeError('LEASE_TOO_LONG')
    c=lease['clock']
    if not 0 <= at-stamp(c['measured_at_master_utc']) <= 35:
        raise RuntimeError('CLOCK_MEASUREMENT_STALE')
    if abs(c['worker_minus_master_seconds'])+c['uncertainty_seconds'] > 0.5:
        raise RuntimeError('CLOCK_BOUND_EXCEEDS_RESERVED_BUDGET')
    if lease.get('safety_pass') is not True:
        raise RuntimeError('LIVE_SAFETY_NOT_PASS')

def onset(plan, actual, clock):
    raw=stamp(actual)-stamp(plan['planned_utc'])
    adjusted=raw-clock['worker_minus_master_seconds']
    upper=abs(adjusted)+clock['uncertainty_seconds']
    result={'planned_master_utc':plan['planned_utc'],'actual_worker_utc':actual,
            'raw_deviation_seconds':raw,'clock_corrected_deviation_seconds':adjusted,
            'uncertainty_seconds':clock['uncertainty_seconds'],'worst_case_deviation_seconds':upper,
            'tolerance_seconds':2,'pass':upper<=2 and abs(raw)<=2}
    return result

def load_plan(path,digest):
    if sha(path)!=digest: raise RuntimeError('PLAN_HASH_MISMATCH')
    p=read(path)
    if p['worker']!=socket.gethostname() or p['scheduler_sha256']!=sha(__file__):
        raise RuntimeError('WORKER_OR_SCHEDULER_MISMATCH')
    if p['mode'] not in ('MARKER','FAULT') or p['tolerance_seconds']!=2:
        raise RuntimeError('UNREGISTERED_MODE_OR_TOLERANCE')
    if p['mode']=='FAULT' and p['id'] not in FAULT_IDS: raise RuntimeError('UNREGISTERED_FAULT')
    if Path(p['runtime']).parent!=BASE/'runtime': raise RuntimeError('BAD_RUNTIME_PATH')
    return p

def run(path,digest):
    process_start=now();plan=load_plan(path,digest);root=Path(plan['runtime'])
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    result={'status':'SCHEDULER_FAILED_CLOSED','process_start_utc':process_start,
            'plan_sha256':digest,'mode':plan['mode'],'fault_action_started':False}
    adapter=None
    def check():
        lease=read(root/'lease.json')
        validate_lease(plan,digest,lease,time.time(),(root/'REVOKED').exists())
        return lease
    def started(actual):
        lease=check();v=onset(plan,actual,lease['clock'])
        v['checked_at_worker_utc']=now();v['plan_sha256']=digest
        save(root/'actual-start.json',v)
        if not v['pass']: raise RuntimeError('ACTUAL_START_EXCEEDS_TWO_SECONDS')
    try:
        lease=check()
        if subprocess.check_output(['timedatectl','show','-p','NTPSynchronized','--value'],text=True).strip()!='yes':
            raise RuntimeError('WORKER_NTP_NOT_SYNCHRONIZED')
        if time.time() < stamp(plan['planned_utc'])-.1: raise RuntimeError('EARLY_TIMER')
        if time.time() > stamp(plan['planned_utc'])+1.5: raise RuntimeError('LATE_TIMER_NO_ACTION')
        save(root/'process-start.json',result)
        if plan['mode']=='MARKER':
            # Optional delay tests late-onset rollback, never a business operation.
            time.sleep(plan.get('test_delay_seconds',0));started(now())
            save(root/'marker.json',{'at_utc':now(),'business_faults_injected':0})
        else:
            if sha(ADAPTER)!=ADAPTER_HASH or sha(plan['registration'])!=plan['registration_sha256']:
                raise RuntimeError('QUALIFIED_ADAPTER_OR_REGISTRATION_CHANGED')
            spec=importlib.util.spec_from_file_location('qualified_adapter',ADAPTER)
            adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
            adapter.IDS[plan['id']]=FAULT_IDS[plan['id']]
            original_save,original_arm,original_memory=adapter.save,adapter.arm,adapter.mem_available
            def checked_save(p,v,replace=False):
                original_save(p,v,replace)
                if v.get('status')=='ACTIVE':
                    result['fault_action_started']=True
                    started(v['actual_start_utc'])
            def checked_arm(*args):
                original_arm(*args);check()
                if abs(time.time()-stamp(plan['planned_utc']))>1.5: raise RuntimeError('PRE_ACTION_TIME_BUDGET_EXCEEDED')
            def checked_memory(): check();return original_memory()
            adapter.save=checked_save;adapter.arm=checked_arm;adapter.mem_available=checked_memory
            injection=adapter.run(Path(plan['registration']),Path(plan['authorization']),Path(plan['output']))
            result['injection']=injection
            if injection['status']!='COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED':
                raise RuntimeError('INJECTION_OR_RECOVERY_FAILED')
        result['status']='SCHEDULED_ACTION_COMPLETE'
    except BaseException as e:
        result['error']=type(e).__name__+': '+str(e)
        if adapter is not None:
            state=adapter.RUNTIME/(plan['id']+'.json')
            if state.exists():
                try: result['cleanup']=adapter.cleanup(state)
                except Exception as ce: result['cleanup_error']=str(ce)
        save(root/'REVOKED',{'at_utc':now(),'reason':result['error']}) if not (root/'REVOKED').exists() else None
    finally:
        result['completed_utc']=now();save(root/'terminal.json',result);print(json.dumps(result),flush=True)
    if result['status']!='SCHEDULED_ACTION_COMPLETE': raise SystemExit(1)

def arm(path,digest):
    plan=load_plan(path,digest)
    if stamp(plan['planned_utc'])-time.time()<10: raise RuntimeError('PLAN_NOT_DEPLOYED_EARLY')
    if not plan['unit'].startswith('ops-a02-') or not plan['unit'].replace('-','').isalnum(): raise RuntimeError('BAD_UNIT')
    calendar=datetime.fromtimestamp(stamp(plan['planned_utc']),timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f UTC')
    cmd=['systemd-run','--unit='+plan['unit'],'--on-calendar='+calendar,
         '--timer-property=AccuracySec=10ms','--timer-property=RandomizedDelaySec=0',
         '--timer-property=Persistent=false','--property=RuntimeMaxSec=330',
         '/usr/bin/python3','-B',str(Path(__file__).resolve()),'run','--plan',str(path),'--sha256',digest]
    subprocess.run(cmd,check=True,capture_output=True,text=True)
    print(subprocess.check_output(['systemctl','show',plan['unit']+'.timer','-p','NextElapseUSecRealtime','-p','AccuracyUSec','-p','ActiveState'],text=True))

def cancel(path,digest):
    p=load_plan(path,digest);root=Path(p['runtime'])
    if not (root/'REVOKED').exists():save(root/'REVOKED',{'at_utc':now(),'reason':'CONTROLLER_CANCEL'})
    # Stop the timer first. A running adapter handles SIGTERM and independent recovery remains armed.
    for suffix in ('.timer','.service'):
        subprocess.run(['systemctl','stop',p['unit']+suffix],capture_output=True,text=True,timeout=20)
    if p['mode']=='FAULT':
        state=ADAPTER.parent.parent/'qualification/private-runtime'/(p['id']+'.json')
        if state.exists():subprocess.run(['/usr/bin/python3','-B',str(ADAPTER),'cleanup','--state',str(state)],check=True,timeout=20)
    active=subprocess.run(['systemctl','is-active',p['unit']+'.timer',p['unit']+'.service'],capture_output=True,text=True)
    if 'active' in active.stdout.splitlines():raise RuntimeError('CANCELLATION_FAILED')
    save(root/'cancellation.json',{'at_utc':now(),'timer_and_service_inactive':True,'unit_states':active.stdout})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('arm','run','cancel'))
    p.add_argument('--plan',type=Path,required=True);p.add_argument('--sha256',required=True);a=p.parse_args()
    globals()[a.action](a.plan,a.sha256)
