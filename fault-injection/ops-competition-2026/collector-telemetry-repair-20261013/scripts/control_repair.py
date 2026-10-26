#!/usr/bin/env python3
"""Sequential no-fault study controller. Independent Master timer covers disconnects."""
import json,sys,time,subprocess
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from run_competition_qualification import remote,optional,copy,LOCAL
ROOT='/root/ops-competition-2026/collector-telemetry-repair-20261013'
DATA='/data/stage5-v3/ops-competition-2026/collector-telemetry-repair-20261013'
HERE=Path(__file__).resolve().parents[1]
def check_result(role,timeout):
    until=time.time()+timeout
    while time.time()<until:
        v=optional('master',DATA+'/runs-v3/'+role+'/execution-result.json')
        if v:
            if v['status']!='VALIDATION_EXECUTION_COMPLETE_PENDING_ASSESSMENT':raise RuntimeError('Execution failed: '+json.dumps(v))
            return v
        time.sleep(5)
    raise RuntimeError('Execution terminal deadline exceeded')
def assess(role):
    print(remote('master',['python3','-B',ROOT+'/scripts/evaluate.py','--role',role],timeout=300),flush=True)
    return optional('master',DATA+'/runs-v3/'+role+'/assessment.json')
def health(ident,allow_timer=False):
    script=HERE/'scripts/refresh_repair_safety.py' if allow_timer else LOCAL/'scripts/refresh_competition_safety.py'
    r=subprocess.run([sys.executable,'-B',str(script),'--id',ident],capture_output=True,text=True,timeout=140)
    print(r.stdout.strip(),flush=True)
    if r.returncode:raise RuntimeError('Actual fresh health failed: '+r.stderr[-1500:])
    return '/data/stage5-v3/ops-competition-2026/audits/'+ident+'/phase-a-decision.json'
def settle(label):
    records=[];good=0;deadline=time.time()+600
    code="import sys,json;sys.path.insert(0,'"+ROOT+"/scripts');from common import *;from run_validation import observe,safety; s=read(DATA/'runs-v3/BASE/schedule.json'); g=pod('otel-collector-'); safety(s['targets']); v=observe(g); print(json.dumps({'at_utc':now(),'status':'SETTLE_HEALTH_PASS','collector_uid':g['metadata']['uid'],'metrics':v}))"
    while time.time()<deadline:
        try:
            value=json.loads(remote('master',['python3','-B','-c',code],timeout=60));records.append(value);good+=1
        except Exception as e:records.append({'status':'SETTLE_WAIT','error':str(e)});good=0
        if good>=3:break
        time.sleep(30)
    path=HERE/'results'/('settle-'+label+'.json')
    with path.open('x') as f:json.dump({'consecutive_passes':good,'records':records},f,indent=2)
    copy('master',path,ROOT+'/results/'+path.name)
    if good<3:raise RuntimeError('Collector/Node telemetry did not stabilize within 600 seconds')
    print(json.dumps({'stage':'STABILIZED','configuration':label,'consecutive_passes':good}),flush=True)
def main():
    terminal=HERE/'results/controller-terminal.json'
    if terminal.exists():raise RuntimeError('Never overwrite a study')
    changed=False;retained=False;armed=False
    result={'status':'COLLECTOR_PATH_BLOCKED','faults_injected':0,'formal_case_created':False}
    try:
        check_result('BASE',850);result['baseline']=assess('BASE')['status']
        h=health('COMP-SF01-REPAIR-APPLY')
        remote('master',['systemd-run','--unit=ops-collector-telemetry-repair-rollback','--on-active=5400s',
            '--timer-property=AccuracySec=1s','--property=Environment=KUBECONFIG=/etc/kubernetes/admin.conf',
            '--property=RuntimeMaxSec=360','/usr/bin/python3','-B',ROOT+'/scripts/change.py','rollback',
            '--output',ROOT+'/results/watchdog-rollback.json'])
        armed=True
        print(remote('master',['python3','-B',ROOT+'/scripts/change.py','apply','--health',h],timeout=330),flush=True);changed=True
        settle('candidate')
        outcomes={}
        for role in ['VAL1','VAL2']:
            remote('master',['systemd-run','--unit=ops-telemetry-repair-'+role.lower(),'--property=RuntimeMaxSec=1500',
                '--property=Environment=KUBECONFIG=/etc/kubernetes/admin.conf','/usr/bin/python3','-B',ROOT+'/scripts/run_validation.py','--role',role])
            print(json.dumps({'stage':'NO_FAULT_VALIDATION_RUNNING','role':role,'duration_seconds':900}),flush=True)
            check_result(role,1400);outcomes[role]=assess(role)
        result['validation_statuses']={k:v['status'] for k,v in outcomes.items()}
        if all(v['status']=='NO_FAULT_VALIDATION_PASS' for v in outcomes.values()):
            h=health('COMP-SF01-REPAIR-POST',True)
            print(remote('master',['python3','-B',ROOT+'/scripts/change.py','retain','--health',h]),flush=True)
            retained=True;result['status']='COLLECTOR_PATH_READY_FOR_PILOT'
        else:
            print(remote('master',['python3','-B',ROOT+'/scripts/change.py','rollback','--output',ROOT+'/results/failed-validation-rollback.json'],timeout=330),flush=True)
            remote('master',['systemctl','stop','ops-collector-telemetry-repair-rollback.timer']);armed=False
            settle('restored-original');result['status']='COLLECTOR_PATH_PARTIAL'
        result['final_health']=health('COMP-SF01-REPAIR-FINAL')
    except BaseException as e:
        result['error']=type(e).__name__+': '+str(e)
        if changed and not retained:
            try:
                result['emergency_rollback']=json.loads(remote('master',['python3','-B',ROOT+'/scripts/change.py','rollback','--output',ROOT+'/results/controller-emergency-rollback.json'],timeout=330))
                remote('master',['systemctl','stop','ops-collector-telemetry-repair-rollback.timer']);armed=False
                settle('emergency-restored')
            except Exception as r:result['rollback_error']=str(r)
        elif armed and not changed:
            try:
                remote('master',['python3','-B',ROOT+'/scripts/change.py','rollback','--output',ROOT+'/results/unapplied-final-rollback.json'],timeout=330)
                remote('master',['systemctl','stop','ops-collector-telemetry-repair-rollback.timer'])
            except Exception as r:result['rollback_error']=str(r)
    finally:
        result['retained_candidate']=retained;result['completed_utc']=datetime.now(timezone.utc).isoformat()
        with terminal.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
        copy('master',terminal,ROOT+'/results/controller-terminal.json');print(json.dumps(result),flush=True)
if __name__=='__main__':main()
