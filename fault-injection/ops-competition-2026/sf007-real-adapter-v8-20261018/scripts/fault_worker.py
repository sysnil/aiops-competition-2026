#!/usr/bin/env python3
"""Draft dedicated SF-007 ServicePause30 capability; never a Marker toggle."""
import argparse,json,os,socket,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
from run_registry import ROOT,BACKEND,load_bundle,action_for,expected_argv,sha,stamp
from handshake import atomic
def now():return datetime.now(timezone.utc).isoformat()
def read(p):return json.loads(Path(p).read_text())
def validate_lease(m,manifest_sha,a,lease,at,revoked=False):
    if revoked:raise RuntimeError('REVOKED')
    if lease.get('manifest_sha256')!=manifest_sha or lease.get('run_id')!=m['run_id'] or lease.get('action_id')!=a['action_id'] or lease.get('permission')!='BOUND_QUALIFIED_FAULT':raise RuntimeError('LEASE_SCOPE')
    if not stamp(lease['issued_utc'])-.5<=at<=stamp(lease['expires_utc']) or stamp(lease['expires_utc'])-stamp(lease['issued_utc'])>4.01:raise RuntimeError('LEASE_EXPIRED')
    if lease.get('safety_pass') is not True or not lease.get('authorization_sha256'):raise RuntimeError('LEASE_UNSAFE')
    clock=lease['clock']
    if not 0<=at-stamp(clock['measured_at_master_utc'])<=35 or abs(clock['worker_minus_master_seconds'])+clock['uncertainty_seconds']>.5:raise RuntimeError('CLOCK_BOUND')
def onset(a,actual,c):
    raw=stamp(actual)-stamp(a['planned_utc']);upper=abs(raw-c['worker_minus_master_seconds'])+c['uncertainty_seconds']
    return {'actual_worker_utc':actual,'planned_master_utc':a['planned_utc'],'raw_deviation_seconds':raw,'worst_case_deviation_seconds':upper,'tolerance_seconds':2,'pass':abs(raw)<=2 and upper<=2,'checked_utc':now()}
def bind(args):
    m,p=load_bundle(args.manifest,args.manifest_sha);a=action_for(m,args.action_id,socket.gethostname())
    if any(p['source_sha256'][str(ROOT/'scripts'/name)]!=sha(ROOT/'scripts'/name) for name in ('fault_worker.py','run_registry.py','handshake.py')):raise RuntimeError('WORKER_SOURCE_CHANGED')
    return m,p,a,Path(a['runtime'])
def arm(args):
    m,p,a,r=bind(args)
    if (r/'REVOKED.json').exists() or (r/'started.json').exists():raise RuntimeError('CANNOT_REARM_CANCELLED_OR_STARTED')
    if stamp(a['planned_utc'])-time.time()<10:raise RuntimeError('NOT_DEPLOYED_EARLY')
    r.mkdir(parents=True,exist_ok=True,mode=0o700)
    calendar=datetime.fromtimestamp(stamp(a['planned_utc']),timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f UTC')
    subprocess.run(['systemd-run','--unit='+a['unit'],'--on-calendar='+calendar,'--timer-property=AccuracySec=10ms','--timer-property=RandomizedDelaySec=0','--timer-property=Persistent=false','--property=RuntimeMaxSec=70',*expected_argv(args.manifest,args.manifest_sha,a['action_id'])],check=True,capture_output=True,text=True)
    atomic(r/'armed.json',{'run_id':m['run_id'],'manifest_sha256':args.manifest_sha,'at_utc':now(),'calendar':calendar},True)
ADAPTER=Path('/root/ops-competition-2026/scripts/competition_adapter.py')
ADAPTER_SHA='791481b3e12ce6f64bb77c75acaff7df8000d800081589194ccde5d5ad3f2a92'
def fire(args):
    import importlib.util
    m,p,a,r=bind(args);adapter=None
    result={'status':'FORMAL_FAULT_FAILED_CLOSED','action_id':a['action_id'],'process_start_utc':now()}
    def check():
        l=read(r/'lease.json');validate_lease(m,args.manifest_sha,a,l,time.time(),(r/'REVOKED.json').exists())
        auth=read(r/'authorization.json')
        if sha(r/'authorization.json')!=l['authorization_sha256'] or auth['permission']!='BOUND_QUALIFIED_FAULT' or auth['action_id']!=a['action_id'] or auth['manifest_sha256']!=args.manifest_sha:raise RuntimeError('AUTHORIZATION_SCOPE')
        return l
    try:
        atomic(r/'started.json',{'process_start_utc':result['process_start_utc'],'manifest_sha256':args.manifest_sha},True)
        check()
        if sha(ADAPTER)!=ADAPTER_SHA:raise RuntimeError('QUALIFIED_ADAPTER_CHANGED')
        if subprocess.check_output(['timedatectl','show','-p','NTPSynchronized','--value'],text=True).strip()!='yes':raise RuntimeError('NTP_NOT_SYNCHRONIZED')
        if not -.1<=time.time()-stamp(a['planned_utc'])<=1.5:raise RuntimeError('PRE_ACTION_ONSET_BUDGET')
        registration=Path(a['qualified_registration']);authpath=r/'authorization.json'
        if sha(registration)!=a['qualified_registration_sha256']:raise RuntimeError('QUALIFIED_PLAN_HASH')
        spec=importlib.util.spec_from_file_location('qualified_adapter',ADAPTER);adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
        adapter.IDS[a['action_id']]=(a['mechanism_id'],30)
        oldsave,oldarm,oldmem=adapter.save,adapter.arm,adapter.mem_available
        def observed_save(path,state,replace=False):
            oldsave(path,state,replace)
            if state.get('status')=='ACTIVE':
                l=check();value=onset(a,state['actual_start_utc'],l['clock'])
                value['actual_target_states']=[adapter.proc(t['pid']) for t in a['targets']]
                value['independent_watchdog_unit']=state.get('watchdog_unit')
                atomic(r/'actual-start.json',value,True)
                if not value['pass']:raise RuntimeError('ONSET_EXCEEDS_TWO_SECONDS_IMMEDIATE_RECOVERY')
        def checked_arm(*arguments):
            oldarm(*arguments);check()
            if abs(time.time()-stamp(a['planned_utc']))>1.5:raise RuntimeError('PRE_ACTION_DEADLINE')
        def checked_mem():check();return oldmem()
        adapter.save=observed_save;adapter.arm=checked_arm;adapter.mem_available=checked_mem
        injection=adapter.run(registration,authpath,Path(a['injection_output']))
        result['injection']=injection
        if injection['status']!='COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED' or injection.get('cleanup',{}).get('status')!='COMPETITION_CLEANUP_ACTIONS_VERIFIED':raise RuntimeError('QUALIFIED_FAULT_OR_CLEANUP_FAILED')
        result.update(status='FORMAL_FAULT_COMPLETE',duration_seconds=injection['duration_seconds'],actual_start_utc=injection['actual_start_utc'])
    except BaseException as e:
        result['error']=type(e).__name__+': '+str(e)
        if adapter:
            state=adapter.RUNTIME/(a['action_id']+'.json')
            if state.exists():
                try:result['emergency_cleanup']=adapter.cleanup(state)
                except Exception as ce:result['cleanup_error']=str(ce)
    finally:
        result['completed_utc']=now();atomic(r/'terminal.json',result,True);print(json.dumps(result),flush=True)
    if result['status']!='FORMAL_FAULT_COMPLETE':raise SystemExit(1)
def cancel(args):
    m,p,a,r=bind(args)
    if not (r/'REVOKED.json').exists():atomic(r/'REVOKED.json',{'at_utc':now(),'manifest_sha256':args.manifest_sha},True)
    for suffix in ('.timer','.service'):subprocess.run(['systemctl','stop',a['unit']+suffix],capture_output=True,text=True,timeout=15)
    v=subprocess.run(['systemctl','is-active',a['unit']+'.timer',a['unit']+'.service'],capture_output=True,text=True)
    if 'active' in v.stdout.splitlines():raise RuntimeError('TASK_STILL_ACTIVE')
    state=Path('/root/ops-competition-2026/qualification/private-runtime')/(a['action_id']+'.json')
    if state.exists():
        if sha(ADAPTER)!=ADAPTER_SHA:raise RuntimeError('CLEANUP_ADAPTER_HASH')
        subprocess.run(['python3','-B',str(ADAPTER),'cleanup','--state',str(state)],check=True,timeout=20)
    if not (r/'cancelled.json').exists():atomic(r/'cancelled.json',{'at_utc':now(),'inactive':True,'lease_revoked':True,'unit_states':v.stdout,'qualified_cleanup_verified':True},True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['arm','fire','cancel']);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--manifest-sha',required=True);p.add_argument('--action-id',required=True);a=p.parse_args();globals()[a.command](a)
