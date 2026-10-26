#!/usr/bin/env python3
"""Real qualification workload/capture on Master; never injects faults itself."""

import argparse
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import urllib.parse
import urllib.request

ROOT=Path('/root/ops-competition-2026')
DATA=Path('/data/stage5-v3/ops-competition-2026')
MOUNT=Path('/data/stage5-v3')
KUBE=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf']
OLD=Path('/root/stage5-v3-autonomous-preparation/scripts')
CG=Path('/root/stage5-v3-clean-gate-v2/scripts')
STATE=Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py')
CONTROLS=ROOT/'scripts/competition_controls.py'
MACHINE=Path('/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml')
PROFILES=Path('/root/stage5-v3-autonomous-preparation/design/load-profiles-c3.yaml')
PEOPLE=Path('/root/cross-layer-workload-generation/design/people.json')
BASE='http://10.104.177.128:8080'
PROM='http://10.97.14.138:9090'
QUALIFICATIONS={'COMP-SF01-SERVICE-SHORT':(180,30,180),'COMP-SF01-SERVICE-300':(300,300,300),
                'COMP-SF01-POD-SHORT':(180,60,180),'COMP-SF01-POD-300':(300,300,300)}

def now():return datetime.now(timezone.utc).isoformat()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    path.chmod(0o600)

def storage():
    fields={k:subprocess.check_output(['findmnt','-n','-o',k,'-T',str(MOUNT)],text=True).strip() for k in ('SOURCE','UUID','OPTIONS')}
    if fields['SOURCE']!='/dev/sdb1' or fields['UUID']!='a35051a2-3d09-4b83-bbc0-3bb055e8a4f3' or 'rw' not in fields['OPTIONS'].split(',') or os.stat(MOUNT).st_dev==os.stat('/').st_dev:
        raise RuntimeError('Approved data mount unavailable')
    rootfree,datafree=shutil.disk_usage('/').free,shutil.disk_usage(MOUNT).free
    if rootfree<5_000_000_000 or datafree-30*1024**3<5_000_000_000:
        raise RuntimeError('30-minute peak budget would breach 5 GB disk floor')
    return {'root_free_bytes':rootfree,'data_free_bytes':datafree,'uuid':fields['UUID']}

def query(q):
    with urllib.request.urlopen(PROM+'/api/v1/query?'+urllib.parse.urlencode({'query':q}),timeout=10) as f:x=json.load(f)
    if x.get('status')!='success':raise RuntimeError('Prometheus query failed')
    return x['data']['result']

def safety(expected):
    nodes=json.loads(subprocess.check_output(KUBE+['get','nodes','-o','json'],text=True,timeout=20))['items']
    if len(nodes)!=4:raise RuntimeError('Node inventory changed')
    for n in nodes:
        c={r['type']:r['status'] for r in n['status']['conditions']}
        if c.get('Ready')!='True' or any(c.get(k)!='False' for k in ('DiskPressure','MemoryPressure','PIDPressure')):
            raise RuntimeError('Node safety state changed')
    pods=json.loads(subprocess.check_output(KUBE+['-n','otel-demo','get','pods','-o','json'],text=True,timeout=20))['items']
    present={p['metadata']['uid']:p for p in pods}
    for service in {t['service'] for t in expected}:
        want={t['uid'] for t in expected if t['service']==service}
        live={p['metadata']['uid'] for p in pods if p['metadata']['labels'].get('opentelemetry.io/name')==service and not p['metadata'].get('deletionTimestamp')}
        if live!=want:raise RuntimeError('Registered service replica universe changed')
    for target in expected:
        p=present.get(target['uid'])
        if not p or p['spec']['nodeName']!=target['node'] or not any(c.get('containerID')==target['container_id'] and c.get('restartCount')==target['restart_count'] for c in p['status'].get('containerStatuses',[])):
            raise RuntimeError('Target UID/container/node/restart changed')
    metrics={}
    for metric in ('k8s_node_cpu_usage','k8s_node_memory_available_bytes','k8s_node_condition_ready'):
        rows=query('timestamp('+metric+')')
        if len(rows)!=4 or any(not 0<=time.time()-float(r['value'][1])<=60 for r in rows):
            raise RuntimeError('Actual node metric source stale: '+metric)
        metrics[metric]=rows
    capacities={n['metadata']['name']:float(n['status']['allocatable']['cpu'][:-1])/1000
        if n['status']['allocatable']['cpu'].endswith('m') else float(n['status']['allocatable']['cpu']) for n in nodes}
    cpu=query('k8s_node_cpu_usage');memory=query('k8s_node_memory_available_bytes')
    if len(cpu)!=4 or len(memory)!=4:raise RuntimeError('Node resource samples incomplete')
    for row in cpu:
        capacity=capacities[row['metric']['k8s_node_name']]
        if float(row['value'][1])>capacity-max(2,capacity*.25):raise RuntimeError('Node CPU reserve breached')
    for row in memory:
        reserve=(4 if row['metric']['k8s_node_name']=='k8s-master' else 8)*1024**3
        if float(row['value'][1])<reserve:raise RuntimeError('Node available-memory reserve breached')
    with urllib.request.urlopen('http://10.111.114.255:9200/_cluster/health',timeout=10) as f:
        if json.load(f)['status']!='green':raise RuntimeError('OpenSearch not green')
    for prefix in ('otel-collector-','otel-cluster-collector-','jaeger-','prometheus-'):
        matches=[p for p in pods if p['metadata']['name'].startswith(prefix)]
        if not matches or any(p['status']['phase']!='Running' or not all(c.get('ready') for c in p['status'].get('containerStatuses',[])) for p in matches):
            raise RuntimeError('Monitoring component unhealthy: '+prefix)
    counters=query('otelcol_receiver_refused_spans_total')
    return {'at_utc':now(),'status':'LIVE_ENVIRONMENT_PASS','disk':storage(),
            'node_metric_source_timestamps':metrics,'collector_refused_raw_series':counters,
            'collector_missing_metric_policy':'UNKNOWN_NOT_ZERO'}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--health',type=Path,required=True);args=parser.parse_args()
    p=json.loads(args.plan.read_text());h=json.loads(args.health.read_text())
    ident=p['id'];expected=QUALIFICATIONS.get(ident)
    if not expected or tuple(p['durations_seconds'])!=expected:raise RuntimeError('Unregistered observation scope/duration')
    if h['status']!='COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING' or h['current_environment_blockers']:
        raise RuntimeError('Actual environment did not pass')
    if not 0<=(datetime.now(timezone.utc)-datetime.fromisoformat(h['at_utc'])).total_seconds()<=180:
        raise RuntimeError('Environment observation stale')
    for path,digest in p['source_sha256'].items():
        if sha(path)!=digest:raise RuntimeError('Observation source version changed: '+path)
    storage();initial=safety(p['targets_and_siblings'])
    out=DATA/'qualification'/ident
    if out.exists():raise RuntimeError('Immutable qualification observation already exists')
    out.mkdir(parents=True,mode=0o700)
    start=datetime.now(timezone.utc)+timedelta(seconds=45)
    offsets=[0,expected[0],expected[0]+expected[1],sum(expected)]
    schedule={'id':ident,'start_utc':start.isoformat(),'end_utc':(start+timedelta(seconds=offsets[-1])).isoformat(),
        'windows':{phase:{'start_utc':(start+timedelta(seconds=offsets[i])).isoformat(),
            'end_utc':(start+timedelta(seconds=offsets[i+1])).isoformat()} for i,phase in enumerate(('before','during','after'))},
        'plan_sha256':sha(args.plan),'source_sha256':p['source_sha256'],'initial_safety':initial,
        'injector_is_separate':True,'formal_case':False}
    save(out/'schedule.json',schedule)
    commands={
        'state-machine':['/usr/bin/python3','-B',str(STATE),'--machine',str(MACHINE),'--profiles',str(PROFILES),
            '--people',str(PEOPLE),'--profile','MEDIUM','--seed',str(p['seed']),'--duration',str(offsets[-1]),
            '--start-utc',start.isoformat(),'--base-url',BASE,'--output',str(out/'state-machine'),'--allow-errors'],
        'controls':['/usr/bin/python3','-B',str(CONTROLS),'--plan',str(args.plan),'--seed',str(p['seed']),
            '--duration',str(offsets[-1]),'--start-utc',start.isoformat(),'--base-url',BASE,'--output',str(out/'controls')]}
    for phase,w in schedule['windows'].items():
        first=out/'capture/captures'/phase;completion=out/'capture/trace-completion'/phase
        commands['first-'+phase]=['/usr/bin/python3','-B',str(OLD/'capture_live_c2.py'),'--phase',phase,
            '--start-utc',w['start_utc'],'--end-utc',w['end_utc'],'--trace-source','JAEGER_OPENSEARCH_STORAGE',
            '--wait-until-end','--output',str(first)]
        commands['completion-'+phase]=['/usr/bin/python3','-B',str(CG/'schedule_trace_completion_v2.py'),
            '--phase',phase,'--start',w['start_utc'],'--end',w['end_utc'],'--first-capture',str(first),'--output',str(completion)]
        commands['audit-'+phase]=['/usr/bin/python3','-B',str(CG/'schedule_trace_v2.py'),'--phase',phase,
            '--start',w['start_utc'],'--end',w['end_utc'],'--first',str(first),'--plus60',str(completion),
            '--output',str(out/'capture/trace-final'/phase),'--audit-output',str(out/'capture/trace-late-audit'/phase)]
    processes={};handles=[];result={'id':ident,'status':'OBSERVATION_FAILED','faults_injected_by_this_program':0}
    for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):
        signal.signal(sig,lambda n,f:(_ for _ in ()).throw(SystemExit(128+n)))
    try:
        for name,command in commands.items():
            stdout=(out/(name+'.stdout')).open('x');stderr=(out/(name+'.stderr')).open('x');handles.extend((stdout,stderr))
            processes[name]=subprocess.Popen(command,stdout=stdout,stderr=stderr)
        with (out/'safety.jsonl').open('x') as stream:
            tick=start.timestamp()
            while tick<=start.timestamp()+offsets[-1]+300:
                time.sleep(max(0,tick-time.time()))
                if time.time()-tick>5:raise RuntimeError('Live monitor deadline missed')
                row=safety(p['targets_and_siblings']);stream.write(json.dumps(row)+'\n');stream.flush()
                if (out/'controls/image-control-failure.json').exists():raise RuntimeError('Independent Image business control failed')
                if any(c.poll() not in (None,0) for c in processes.values()):raise RuntimeError('Capture or load process failed')
                if all(c.poll() is not None for c in processes.values()):break
                tick+=15
        result['exit_codes']={n:c.wait(timeout=15) for n,c in processes.items()}
        if any(result['exit_codes'].values()):raise RuntimeError('Observation incomplete')
        if any(sha(path)!=digest for path,digest in p['source_sha256'].items()):raise RuntimeError('Observation source lock changed during run')
        result['status']='OBSERVATION_COMPLETE_NOT_FAULT_QUALIFICATION'
    except BaseException as error:
        result['error']=type(error).__name__+': '+str(error)
    finally:
        for child in processes.values():
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.kill()
        for handle in handles:handle.close()
        result['completed_utc']=now();save(out/'observation-result.json',result)
        print(json.dumps(result),flush=True)
    if result['status']!='OBSERVATION_COMPLETE_NOT_FAULT_QUALIFICATION':raise SystemExit(1)

if __name__=='__main__':main()
