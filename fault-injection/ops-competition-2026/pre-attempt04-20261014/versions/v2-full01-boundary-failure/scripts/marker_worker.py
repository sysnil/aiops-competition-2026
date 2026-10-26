#!/usr/bin/env python3
"""Harmless-only capability: no adapter import, no business PID signals, no arbitrary command."""
import argparse,json,os,socket,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
from run_registry import ROOT,BACKEND,load_bundle,action_for,expected_argv,sha,stamp
from handshake import atomic
def now():return datetime.now(timezone.utc).isoformat()
def read(p):return json.loads(Path(p).read_text())
def validate_lease(m,manifest_sha,a,lease,at,revoked=False):
    if revoked:raise RuntimeError('REVOKED')
    if lease.get('manifest_sha256')!=manifest_sha or lease.get('run_id')!=m['run_id'] or lease.get('action_id')!=a['action_id'] or lease.get('permission')!='MARKER_ONLY':raise RuntimeError('LEASE_SCOPE')
    if not stamp(lease['issued_utc'])-.5<=at<=stamp(lease['expires_utc']) or stamp(lease['expires_utc'])-stamp(lease['issued_utc'])>4.01:raise RuntimeError('LEASE_EXPIRED')
    if lease.get('safety_pass') is not True or not lease.get('authorization_sha256'):raise RuntimeError('LEASE_UNSAFE')
    clock=lease['clock']
    if not 0<=at-stamp(clock['measured_at_master_utc'])<=35 or abs(clock['worker_minus_master_seconds'])+clock['uncertainty_seconds']>.5:raise RuntimeError('CLOCK_BOUND')
def onset(a,actual,c):
    raw=stamp(actual)-stamp(a['planned_utc']);upper=abs(raw-c['worker_minus_master_seconds'])+c['uncertainty_seconds']
    return {'actual_worker_utc':actual,'planned_master_utc':a['planned_utc'],'raw_deviation_seconds':raw,'worst_case_deviation_seconds':upper,'tolerance_seconds':2,'pass':abs(raw)<=2 and upper<=2,'checked_utc':now()}
def bind(args):
    m,p=load_bundle(args.manifest,args.manifest_sha);a=action_for(m,args.action_id,socket.gethostname())
    if any(p['source_sha256'][str(ROOT/'scripts'/name)]!=sha(ROOT/'scripts'/name) for name in ('marker_worker.py','run_registry.py','handshake.py')):raise RuntimeError('WORKER_SOURCE_CHANGED')
    return m,p,a,Path(a['runtime'])
def arm(args):
    m,p,a,r=bind(args)
    if (r/'REVOKED.json').exists() or (r/'started.json').exists():raise RuntimeError('CANNOT_REARM_CANCELLED_OR_STARTED')
    if stamp(a['planned_utc'])-time.time()<10:raise RuntimeError('NOT_DEPLOYED_EARLY')
    r.mkdir(parents=True,exist_ok=True,mode=0o700)
    calendar=datetime.fromtimestamp(stamp(a['planned_utc']),timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f UTC')
    subprocess.run(['systemd-run','--unit='+a['unit'],'--on-calendar='+calendar,'--timer-property=AccuracySec=10ms','--timer-property=RandomizedDelaySec=0','--timer-property=Persistent=false','--property=RuntimeMaxSec=25',*expected_argv(args.manifest,args.manifest_sha,a['action_id'])],check=True,capture_output=True,text=True)
    atomic(r/'armed.json',{'run_id':m['run_id'],'manifest_sha256':args.manifest_sha,'at_utc':now(),'calendar':calendar},True)
def fire(args):
    m,p,a,r=bind(args);result={'status':'MARKER_FAILED_CLOSED','business_faults_injected':0,'action_id':a['action_id'],'process_start_utc':now()}
    def check():
        l=read(r/'lease.json');validate_lease(m,args.manifest_sha,a,l,time.time(),(r/'REVOKED.json').exists())
        auth=read(r/'authorization.json')
        if sha(r/'authorization.json')!=l['authorization_sha256'] or auth['permission']!='MARKER_ONLY' or auth['action_id']!=a['action_id'] or auth['manifest_sha256']!=args.manifest_sha:raise RuntimeError('AUTHORIZATION_SCOPE')
        if not 0<=time.time()-stamp(auth['at_utc'])<=180:raise RuntimeError('AUTHORIZATION_STALE')
        return l
    try:
        lease=check()
        if subprocess.check_output(['timedatectl','show','-p','NTPSynchronized','--value'],text=True).strip()!='yes':raise RuntimeError('NTP_NOT_SYNCHRONIZED')
        actual=now();v=onset(a,actual,lease['clock']);atomic(r/'actual-start.json',v,True)
        if not v['pass']:raise RuntimeError('ONSET_EXCEEDS_TWO_SECONDS')
        atomic(r/'started.json',{'at_utc':actual,'action':'harmless local time marker','backend':BACKEND},True)
        began=time.monotonic()
        while time.monotonic()-began<a['duration_seconds']:check();time.sleep(.2)
        result.update(status='MARKER_COMPLETE',duration_seconds=time.monotonic()-began,actual_start_utc=actual)
    except BaseException as e:result['error']=type(e).__name__+': '+str(e)
    finally:
        result['completed_utc']=now();atomic(r/'terminal.json',result,True);print(json.dumps(result))
    if result['status']!='MARKER_COMPLETE':raise SystemExit(1)
def cancel(args):
    m,p,a,r=bind(args)
    if not (r/'REVOKED.json').exists():atomic(r/'REVOKED.json',{'at_utc':now(),'manifest_sha256':args.manifest_sha},True)
    for suffix in ('.timer','.service'):subprocess.run(['systemctl','stop',a['unit']+suffix],capture_output=True,text=True,timeout=15)
    v=subprocess.run(['systemctl','is-active',a['unit']+'.timer',a['unit']+'.service'],capture_output=True,text=True)
    if 'active' in v.stdout.splitlines():raise RuntimeError('TASK_STILL_ACTIVE')
    if not (r/'cancelled.json').exists():atomic(r/'cancelled.json',{'at_utc':now(),'inactive':True,'lease_revoked':True,'unit_states':v.stdout,'business_recovery_tested':False},True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['arm','fire','cancel']);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--manifest-sha',required=True);p.add_argument('--action-id',required=True);a=p.parse_args();globals()[a.command](a)
