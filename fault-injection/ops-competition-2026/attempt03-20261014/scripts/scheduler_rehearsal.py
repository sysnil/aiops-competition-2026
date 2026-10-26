"""Six real harmless timers plus revoked and delayed-onset cancellation tests."""
import json,time,subprocess
from datetime import datetime,timezone
from pathlib import Path
from master_transport import *
from local_scheduler import sha,save

def main():
    output=BASE/'results/scheduler-rehearsal.json'
    if output.exists():raise RuntimeError('Immutable test results exist')
    proofs={};plans=[];failures=[];rows=[]
    try:
        for host in ('w2','w3'):
            remote(host,['mkdir','-p',BASE/'scripts',BASE/'plans',BASE/'runtime'])
            copy(host,BASE/'scripts/local_scheduler.py',BASE/'scripts/local_scheduler.py')
            proofs[host]=clock(host)
            if abs(proofs[host]['worker_minus_master_seconds'])+proofs[host]['uncertainty_seconds']>.5:raise RuntimeError('CLOCK_BOUND')
            ntp=remote(host,['timedatectl','show','-p','NTPSynchronized','-p','NTP'])
            save(BASE/'results'/('clock-'+host+'.json'),{'measurement':proofs[host],'ntp':ntp})
        base=time.time()+30
        # Last two markers test cancellation before dispatch and immediate rejection of >2s onset.
        for host in ('w2','w3'):
            for i in range(5):
                name='marker-'+host+'-'+str(i+1);p={'id':name,'mode':'MARKER','worker':'k8s-worker'+host[-1],
                    'unit':'ops-a03-'+name,'runtime':str(BASE/'runtime'/name),'tolerance_seconds':2,
                    'planned_utc':datetime.fromtimestamp(base+i*20,timezone.utc).isoformat(),
                    'scheduler_sha256':sha(BASE/'scripts/local_scheduler.py'),'test_delay_seconds':2.3 if i==3 else 0}
                file=BASE/'plans'/(name+'.json');save(file,p);digest=sha(file);copy(host,file,file)
                armed=remote(host,['python3','-B',BASE/'scripts/local_scheduler.py','arm','--plan',file,'--sha256',digest])
                plans.append({'host':host,'plan':p,'file':str(file),'sha256':digest,'armed':armed,'index':i})
        observed=set();cancelled=set();late_cancelled=set();refresh=time.monotonic()
        while time.time()<base+90:
            if time.monotonic()-refresh>=15:
                proofs={h:clock(h,3) for h in ('w2','w3')};refresh=time.monotonic()
            for item in plans:
                h,p=item['host'],item['plan'];key=p['id'];idx=item['index']
                if key in cancelled:continue
                # A failed predecessor MUST cancel the later predeployed marker.
                if idx==4 and h in late_cancelled:
                    remote(h,['python3','-B',BASE/'scripts/local_scheduler.py','cancel','--plan',item['file'],'--sha256',item['sha256']]);cancelled.add(key);continue
                if time.time()<=datetime.fromisoformat(p['planned_utc']).timestamp()+5:
                    lease(h,p,item['sha256'],proofs[h])
                terminal=optional(h,Path(p['runtime'])/'terminal.json')
                actual=optional(h,Path(p['runtime'])/'actual-start.json')
                if actual and key not in observed:
                    actual['controller_seen_master_utc']=now()
                    actual['feedback_upper_seconds']=time.time()-datetime.fromisoformat(actual['actual_worker_utc']).timestamp()+proofs[h]['worker_minus_master_seconds']+proofs[h]['uncertainty_seconds']
                    rows.append({'id':key,'host':h,'index':idx,'actual':actual});observed.add(key)
                    if idx<3 and (not actual['pass'] or actual['feedback_upper_seconds']>2):failures.append(key+':onset_or_feedback')
                if terminal:
                    if idx<3 and terminal['status']!='SCHEDULED_ACTION_COMPLETE':failures.append(key+':terminal')
                    if idx==3:
                        if terminal['status']!='SCHEDULER_FAILED_CLOSED' or 'ACTUAL_START_EXCEEDS_TWO_SECONDS' not in terminal.get('error',''):failures.append(key+':late_test')
                        late_cancelled.add(h)
            time.sleep(.15)
        for item in plans:
            h,p=item['host'],item['plan'];root=Path(p['runtime']);idx=item['index']
            terminal=optional(h,root/'terminal.json');marker=optional(h,root/'marker.json')
            if idx<3 and (not terminal or terminal['status']!='SCHEDULED_ACTION_COMPLETE' or not marker):failures.append(p['id']+':missing_success')
            if idx>=3 and marker:failures.append(p['id']+':unexpected_marker')
            if idx==4 and not optional(h,root/'cancellation.json'):failures.append(p['id']+':missing_cancel')
            journal=remote(h,['journalctl','-u',p['unit']+'.service','-u',p['unit']+'.timer','--no-pager','-o','short-iso-precise'])
            save(BASE/'results'/(p['id']+'.json'),{'plan':item,'terminal':terminal,'journal':journal,'marker':marker})
        if len([r for r in rows if r['index']<3])!=6:failures.append('NOT_SIX_STARTS')
    except BaseException as e:failures.append(type(e).__name__+': '+str(e))
    finally:
        for item in plans:
            try:
                if not optional(item['host'],Path(item['plan']['runtime'])/'cancellation.json'):
                    remote(item['host'],['python3','-B',BASE/'scripts/local_scheduler.py','cancel','--plan',item['file'],'--sha256',item['sha256']])
            except Exception as e:failures.append('cleanup:'+str(e))
        result={'status':'NO_FAULT_SCHEDULER_PASS' if not failures else 'NO_FAULT_SCHEDULER_FAILED','failures':failures,'rows':rows,'clock':proofs,'business_faults_injected':0,'at_utc':now()}
        save(output,result);print(json.dumps(result),flush=True)
    if failures:raise SystemExit(1)
if __name__=='__main__':main()
