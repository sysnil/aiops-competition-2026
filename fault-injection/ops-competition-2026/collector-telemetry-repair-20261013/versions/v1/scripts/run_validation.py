"""Bounded, independently seeded ordinary load; no fault API or injection mechanism."""
import argparse,json,sys,time,signal,subprocess,gzip
from datetime import datetime,timedelta,timezone
from pathlib import Path
from common import *
sys.path.insert(0,'/root/ops-competition-2026/scripts')
from competition_observation import (STATE,CONTROLS,MACHINE,PROFILES,PEOPLE,OLD,CG,BASE,safety,storage)

def observe(gateway):
    name=gateway['metadata']['name']
    query='{k8s_pod_name="'+name+'",__name__=~"otelcol_(receiver_.*|processor_.*|exporter_.*|process_.*)"}'
    values=prom(query)
    mem=prom('container_memory_working_set_bytes{namespace="otel-demo",pod="'+name+'",container="opentelemetry-collector"}')
    cpu=prom('rate(container_cpu_usage_seconds_total{namespace="otel-demo",pod="'+name+'",container="opentelemetry-collector"}[1m])')
    rows=mem['data']['result']
    if len(rows)!=1 or float(rows[0]['value'][1])>=380*1024**2:raise RuntimeError('Collector memory bound or identity failed')
    live=pod('otel-collector-')
    if live['metadata']['uid']!=gateway['metadata']['uid'] or live['status']['containerStatuses']!=gateway['status']['containerStatuses']:
        # Ready transition, restart, new container, or termination is never hidden.
        a=[(r['containerID'],r['restartCount'],r['ready']) for r in gateway['status']['containerStatuses']]
        b=[(r['containerID'],r['restartCount'],r['ready']) for r in live['status']['containerStatuses']]
        if live['metadata']['uid']!=gateway['metadata']['uid'] or a!=b:raise RuntimeError('Collector identity/health changed')
    latest={}
    for label,index,field in [('logs','otel-logs-*','@timestamp'),('traces','jaeger-span-*','startTime')]:
        response=http(OS+'/'+index+'/_search',{'size':1,'sort':[{field:'desc'}],'query':{'match_all':{}}})
        value=response['hits']['hits'][0]['_source'][field]
        ts=datetime.fromisoformat(value.replace('Z','+00:00')).timestamp() if label=='logs' else value/1e6
        latest[label]={'source_time':value,'age_seconds':time.time()-ts}
    stats={}
    for line in admin('stats?filter=tracing%7Copentelemetry_collector_grpc').splitlines():
        if ': ' in line:
            k,v=line.rsplit(': ',1)
            if v.isdigit():stats[k]=int(v)
    return {'at_utc':now(),'collector_metrics':values,'working_set':mem,'cpu':cpu,'latest_storage':latest,'envoy_stats':stats}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--role',choices=['BASE','VAL1','VAL2'],required=True);a=parser.parse_args()
    plan=read(ROOT/'plans/validation-v1.json');setting=plan['runs'][a.role]
    for p,h in plan['source_sha256'].items():
        if sha(p)!=h:raise RuntimeError('Registered source changed')
    relay=obj('configmap','otel-collector')['data']['relay']
    if a.role=='BASE':expected=read(DATA/'diagnosis-v2/configmap-otel-collector.json')['data']['relay']
    else:expected=read(ROOT/'plans/change-plan.json')['new_relay']
    if relay!=expected:raise RuntimeError('Unexpected Collector config')
    gateway=pod('otel-collector-');proxy=pod('frontend-proxy-')
    targets=[]
    for p in obj('pods')['items']:
        service=p['metadata']['labels'].get('opentelemetry.io/name')
        if service in ('frontend','recommendation'):
            c=p['status']['containerStatuses'][0]
            targets.append({'uid':p['metadata']['uid'],'service':service,'node':p['spec']['nodeName'],
                'container_id':c['containerID'],'restart_count':c['restartCount']})
    storage();safety(targets);initial=observe(gateway)
    out=DATA/'runs'/a.role
    if out.exists():raise RuntimeError('No overwrite or implicit rerun')
    out.mkdir(parents=True,mode=0o700)
    start=datetime.now(timezone.utc)+timedelta(seconds=45);duration=setting['duration_seconds']
    end=start+timedelta(seconds=duration+30)
    windows={f'chunk-{i:02d}':{'start_utc':(start+timedelta(seconds=i*300)).isoformat(),
        'end_utc':(start+timedelta(seconds=min((i+1)*300,duration)+(30 if (i+1)*300==duration else 0))).isoformat()}
        for i in range(duration//300)}
    schedule={'role':a.role,**setting,'start_utc':start.isoformat(),'load_end_utc':(start+timedelta(seconds=duration)).isoformat(),
        'end_utc':end.isoformat(),'windows':windows,'collector':gateway,'proxy':proxy,'targets':targets,
        'config_sha256':hashlib.sha256(relay.encode()).hexdigest(),'plan_sha256':sha(ROOT/'plans/validation-v1.json'),
        'initial_monitor':initial,'faults_injected':0}
    save(out/'schedule.json',schedule)
    command_plan=ROOT/'plans/validation-v1.json'
    commands={'workload':['python3','-B',str(STATE),'--machine',str(MACHINE),'--profiles',str(PROFILES),'--people',str(PEOPLE),
        '--profile','MEDIUM','--seed',str(setting['seed']),'--duration',str(duration),'--start-utc',start.isoformat(),'--base-url',BASE,'--output',str(out/'state-machine')],
        'controls':['python3','-B',str(CONTROLS),'--plan',str(command_plan),'--seed',str(setting['seed']),
            '--duration',str(duration),'--start-utc',start.isoformat(),'--base-url',BASE,'--output',str(out/'controls')]}
    for name,w in windows.items():
        base=out/'capture'/name;first=base/'captures/during';middle=base/'trace-completion/during'
        commands[name+'-first']=['python3','-B',str(OLD/'capture_live_c2.py'),'--phase','during','--start-utc',w['start_utc'],
            '--end-utc',w['end_utc'],'--trace-source','JAEGER_OPENSEARCH_STORAGE','--wait-until-end','--output',str(first)]
        commands[name+'-plus60']=['python3','-B',str(CG/'schedule_trace_completion_v2.py'),'--phase','during','--start',w['start_utc'],
            '--end',w['end_utc'],'--first-capture',str(first),'--output',str(middle)]
        commands[name+'-audit']=['python3','-B',str(CG/'schedule_trace_v2.py'),'--phase','during','--start',w['start_utc'],
            '--end',w['end_utc'],'--first',str(first),'--plus60',str(middle),'--output',str(base/'trace-final/during'),
            '--audit-output',str(base/'trace-late-audit/during')]
    processes={};handles=[];result={'status':'VALIDATION_EXECUTION_FAILED','role':a.role,'faults_injected':0}
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):signal.signal(sig,lambda n,f:(_ for _ in ()).throw(SystemExit(n)))
    try:
        for name,cmd in commands.items():
            f=(out/(name+'.stdout')).open('x');e=(out/(name+'.stderr')).open('x');handles.extend([f,e])
            processes[name]=subprocess.Popen(cmd,stdout=f,stderr=e,env={**__import__('os').environ,'KUBECONFIG':'/etc/kubernetes/admin.conf'})
        tick=start.timestamp()
        with (out/'monitor.jsonl').open('x') as stream:
            while tick<=end.timestamp()+300:
                time.sleep(max(0,tick-time.time()))
                if time.time()-tick>5:raise RuntimeError('Monitor missed fixed schedule')
                row=observe(gateway);row['safety']=safety(targets);stream.write(json.dumps(row)+'\n');stream.flush()
                if any(c.poll() not in (None,0) for c in processes.values()):raise RuntimeError('Load/capture failed')
                if all(c.poll() is not None for c in processes.values()):break
                tick+=15
        codes={k:p.wait(timeout=15) for k,p in processes.items()}
        if any(codes.values()):raise RuntimeError('Incomplete capture')
        if obj('configmap','otel-collector')['data']['relay']!=expected or pod('frontend-proxy-')['metadata']['uid']!=proxy['metadata']['uid']:
            raise RuntimeError('Collector configuration or Proxy identity drift')
        result.update(status='VALIDATION_EXECUTION_COMPLETE_PENDING_ASSESSMENT',exit_codes=codes)
    except BaseException as e:result['error']=type(e).__name__+': '+str(e)
    finally:
        for p in processes.values():
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:p.kill()
        for f in handles:f.close()
        for prefix in ('otel-collector-','frontend-proxy-','jaeger-'):
            try:
                name=pod(prefix)['metadata']['name']
                raw=kube('logs',name,'--since-time='+start.isoformat())
                with gzip.open(out/(prefix+'runtime.log.gz'),'wt') as f:f.write(raw)
            except Exception as e:result.setdefault('log_capture_errors',{})[prefix]=str(e)
        result['completed_utc']=now();save(out/'execution-result.json',result);print(json.dumps(result),flush=True)
    if result['status']!='VALIDATION_EXECUTION_COMPLETE_PENDING_ASSESSMENT':raise SystemExit(1)
if __name__=='__main__':main()
