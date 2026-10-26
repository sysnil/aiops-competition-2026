"""Single soft-limit candidate; guarded compare-and-swap and independent rollback."""
import argparse,os,importlib.util,subprocess,json
from datetime import datetime,timezone
import yaml
from common import *
TIMER='ops-collector-telemetry-repair-rollback.timer'
def module():
    os.environ['KUBECONFIG']='/etc/kubernetes/admin.conf'
    spec=importlib.util.spec_from_file_location('guarded_original','/root/stage5-v3-collector-reliability/scripts/collector_change.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.ROOT=ROOT;m.TIMER=TIMER;m.ANNOTATION='competition-collector-repair'
    return m
def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','rollback-selftest','apply','rollback','retain'])
    p.add_argument('--health');p.add_argument('--output');a=p.parse_args();m=module()
    if a.action=='prepare':
        m.backup(DATA/'prechange-backup')
        prereg=ROOT/'plans/change-preregistration.json'
        save(prereg,{'status':'PREREGISTERED_BEFORE_COLLECTOR_CHANGE','single_candidate':{'old':25,'new':15},
            'validation_plan_sha256':sha(ROOT/'plans/validation-v2.json')})
        print(json.dumps(m.plan(DATA/'prechange-backup',prereg,ROOT/'plans/change-plan.json')));return
    plan=read(ROOT/'plans/change-plan.json')
    if a.action in ('rollback-selftest','rollback'):
        if a.action=='rollback':
            for role in ('BASE','VAL1','VAL2'):
                subprocess.run(['systemctl','stop','ops-telemetry-repair-'+role.lower()+'.service'],capture_output=True,timeout=30)
        output=Path(a.output) if a.output else ROOT/'results/automatic-rollback.json'
        result=m.rollback(plan,output);print(json.dumps(result));return
    if a.action=='apply':
        h=read(a.health);age=(datetime.now(timezone.utc)-datetime.fromisoformat(h['at_utc'])).total_seconds()
        if h['status']!='COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING' or h['current_environment_blockers'] or not 0<=age<=180:
            raise RuntimeError('Fresh actual environment safety required')
        proof=read(ROOT/'results/rollback-selftest.json')
        if proof['status']!='COLLECTOR_ORIGINAL_CONFIG_RESTORED':raise RuntimeError('Systemd rollback self-test not proven')
        if not m.timer_active():raise RuntimeError('Independent rollback timer not active')
        v=read(ROOT/'plans/validation-v2.json')
        if any(sha(p)!=digest for p,digest in v['source_sha256'].items()):raise RuntimeError('Registered sources changed')
        try:
            m.patch_configmap(plan['original_relay'],plan['new_relay'],plan['original_configmap_uid'])
            m.patch_annotation(plan,True);state=m.verify_live(plan,True)
            save(ROOT/'results/applied.json',{'status':'SINGLE_CANDIDATE_APPLIED','at_utc':now(),'state':state,'faults_injected':0,'plan_sha256':sha(ROOT/'plans/change-plan.json')})
        except BaseException:
            m.rollback(plan,ROOT/'results/apply-failure-rollback.json');raise
        print(json.dumps({'status':'SINGLE_CANDIDATE_APPLIED','state':state}));return
    if a.action=='retain':
        for role in ('VAL1','VAL2'):
            if read(DATA/'runs'/role/'assessment.json')['status']!='NO_FAULT_VALIDATION_PASS':raise RuntimeError('Both independent validations must pass')
        state=m.verify_live(plan,True)
        # Caller must provide a fresh post-validation safety decision allowing only this timer.
        h=read(a.health);age=(datetime.now(timezone.utc)-datetime.fromisoformat(h['at_utc'])).total_seconds()
        if h['status']!='REPAIR_POST_HEALTH_PASS' or h.get('blockers') or not 0<=age<=180:raise RuntimeError('Fresh final safety required')
        subprocess.run(['systemctl','stop',TIMER],check=True,timeout=15)
        if m.timer_active():raise RuntimeError('Rollback timer still active')
        save(ROOT/'results/retained.json',{'status':'VERIFIED_REPAIR_RETAINED','at_utc':now(),'state':state,'rollback_command':'python3 -B '+str(ROOT/'scripts/change.py')+' rollback','faults_injected':0})
        print(json.dumps({'status':'VERIFIED_REPAIR_RETAINED'}))
if __name__=='__main__':main()
