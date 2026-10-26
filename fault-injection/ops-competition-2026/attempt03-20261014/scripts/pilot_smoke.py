"""Short ordinary-load software/readiness smoke, never a production case or fault."""
from datetime import timedelta
import subprocess,sys
from pilot_common import *
from competition_observation import STATE,MACHINE,PROFILES,PEOPLE,BASE,OLD,CG,storage
def main():
    out=DATA/'attempt03-20261014/readiness-smoke'
    if out.exists():raise RuntimeError('Preserve smoke attempts')
    storage();initial=telemetry_state();out.mkdir(parents=True,mode=0o700)
    start=datetime.now(timezone.utc)+timedelta(seconds=15);end=start+timedelta(seconds=75);capture_start=start-timedelta(seconds=1)
    plan={'workload':{'duration_seconds':60,'control_streams':[{'name':'image_asset','rate_per_second':1,'seed_offset':101},
        {'name':'cart_view','rate_per_second':2,'seed_offset':103}],'control_timeout_seconds':8,
        'control_concurrency_per_stream':{'image_asset':2,'cart_view':4}}}
    save(out/'control-plan.json',plan);save(out/'schedule.json',{'start_utc':start.isoformat(),'end_utc':end.isoformat(),
        'seed':1007100,'faults_injected':0,'formal_case_created':False,'rate_unchanged':True})
    processes=[];handles=[]
    commands={'state':['python3','-B',str(PROD/'scripts/pilot_workload.py'),'--machine',str(MACHINE),'--profiles',str(PROFILES),
        '--people',str(PEOPLE),'--profile','MEDIUM','--seed','1007100','--duration','60','--start-utc',start.isoformat(),
        '--base-url',BASE,'--output',str(out/'state-machine'),'--allow-errors'],
        'controls':['python3','-B',str(PROD/'scripts/pilot_controls.py'),'--plan',str(out/'control-plan.json'),'--seed','1007100',
            '--duration','60','--start-utc',start.isoformat(),'--base-url',BASE,'--output',str(out/'controls')],
        'capture':['python3','-B',str(OLD/'capture_live_c2.py'),'--phase','before','--start-utc',capture_start.isoformat(),
            '--end-utc',end.isoformat(),'--trace-source','JAEGER_OPENSEARCH_STORAGE','--wait-until-end','--output',str(out/'capture/before')],
        'completion':['python3','-B',str(CG/'schedule_trace_completion_v2.py'),'--phase','before','--start',capture_start.isoformat(),
            '--end',end.isoformat(),'--first-capture',str(out/'capture/before'),'--output',str(out/'completion/before')]}
    result={'status':'SMOKE_FAILED','faults_injected':0,'formal_case_created':False}
    try:
        for name,cmd in commands.items():
            f=(out/(name+'.stdout')).open('x');e=(out/(name+'.stderr')).open('x');handles.extend([f,e])
            processes.append(subprocess.Popen(cmd,stdout=f,stderr=e,env={**__import__('os').environ,'KUBECONFIG':'/etc/kubernetes/admin.conf'}))
        for p in processes:
            if p.wait(timeout=180):raise RuntimeError('Smoke process failed')
        sys.path.insert(0,str(CG));from trace_identity import span_map
        spans=span_map(out/'completion/before');proxy={k[0] for k,(_,s) in spans.items() if s['process']['serviceName']=='frontend-proxy' and s.get('tag',{}).get('span@kind')=='server'}
        requests=live_requests(out);expected={r['trace_id'].lower() for r in requests}
        if len(requests)!=len(expected) or expected-proxy or not all(r['success'] for r in requests):raise RuntimeError('Live request interface, business health or trace identity failed')
        current=telemetry_state(initial['uid'])
        if current['refused']!=initial['refused']:raise RuntimeError('Receiver refused during smoke')
        result.update(status='PRODUCTION_SOFTWARE_AND_LIVE_INPUT_SMOKE_PASS',requests=len(requests),proxy_matched=len(expected),
            initial_collector=initial,current_collector=current,public_user_agent_only_change=True,
            capture_manifest_sha256=sha(out/'capture/before/capture-manifest.json'))
    except Exception as e:result['error']=str(e)
    finally:
        for p in processes:
            if p.poll() is None:p.terminate()
        for f in handles:f.close()
        save(out/'result.json',result);print(json.dumps({k:v for k,v in result.items() if k not in ('initial_collector','current_collector')}))
    if result['status']!='PRODUCTION_SOFTWARE_AND_LIVE_INPUT_SMOKE_PASS':raise SystemExit(1)
if __name__=='__main__':main()
