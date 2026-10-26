#!/usr/bin/env python3
"""One preregistered hour, continuous ordinary load, bounded immutable capture chunks.

No injection capability. The qualified Worker adapter owns all fault recovery.
This source is not authorized to run before both mechanism qualifications pass.
"""
import argparse
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import signal
import subprocess
import time
from pilot_common import OPS,PROD,RUN,TELEMETRY,OFFSETS,telemetry_state,live_requests,CONTROL,RUN_ID,REGISTRY,MANIFEST_PATH,MANIFEST_SHA
import sys
sys.path.append(str(OPS/'scripts'))
from competition_observation import (ROOT,DATA,OLD,CG,STATE,CONTROLS,MACHINE,
    PROFILES,PEOPLE,BASE,storage,safety,save,sha,now)

OLD=CG=Path('/root/ops-competition-2026/sf002-production-20261014/capture')

def main():
    p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--manifest-sha',required=True);p.add_argument('--health',type=Path,required=True);a=p.parse_args()
    from run_registry import load_bundle
    manifest,checked_plan=load_bundle(a.manifest,a.manifest_sha,RUN_ID)
    if str(a.manifest)!=MANIFEST_PATH or a.manifest_sha!=MANIFEST_SHA:raise RuntimeError('OBSERVER_REGISTRY_DIVERGENCE')
    a.plan=Path(manifest['plan_path'])
    from handshake import atomic,publish
    process_started=now()
    plan=json.loads(a.plan.read_text());health=json.loads(a.health.read_text())
    atomic(CONTROL/'initializing.json',{'run_id':RUN_ID,'pid':__import__('os').getpid(),'started_utc':process_started},True)
    telemetry=TELEMETRY
    if json.loads(telemetry.read_text()).get('status')!='COLLECTOR_PATH_READY_FOR_PILOT' or sha(telemetry)!=plan.get('production_telemetry_proof_sha256'):
        raise RuntimeError('Production capture readiness blocked or proof changed')
    if plan['id']!=RUN_ID or plan['duration_seconds']!=3600 or plan['formal_case'] is not True:raise RuntimeError('Only registered ATTEMPT04 hour allowed')
    if health['status']!='COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING' or health['current_environment_blockers']:raise RuntimeError('Actual safety failed')
    if not 0<=(datetime.now(timezone.utc)-datetime.fromisoformat(health['at_utc'])).total_seconds()<=180:raise RuntimeError('Safety stale')
    for path,digest in plan['source_sha256'].items():
        if sha(path)!=digest:raise RuntimeError('Registered source changed')
    for proof in plan['prerequisites']:
        if sha(proof['file'])!=proof['sha256'] or json.loads(Path(proof['file']).read_text()).get('qualified') is not True:raise RuntimeError('Mechanism not qualified')
    if {r['id'] for r in plan['prerequisites']}!={'COMP-SF01-SERVICE-300','COMP-SF01-POD-300'}:raise RuntimeError('Both mechanisms required')
    storage();check_started=now();initial=safety(plan['targets_and_siblings']); initial_telemetry=telemetry_state(plan['collector_uid']);out=RUN
    if out.exists():raise RuntimeError('Never overwrite or retry a case')
    out.mkdir(parents=True,mode=0o700)
    start=datetime.fromisoformat(plan['start_utc']);duration=plan['duration_seconds'];offsets=plan['capture_offsets_seconds']
    if time.time()>=start.timestamp()-10:raise RuntimeError('Prestart preparation missed fixed T0')
    from storage_budget import runtime_check
    sequence=0
    publish(CONTROL,plan,sha(a.plan),sha(__file__),sequence,check_started,{'environment':initial,'collector':initial_telemetry})
    sequence+=1
    schedule={'id':plan['id'],'start_utc':start.isoformat(),'end_utc':(start+timedelta(seconds=duration)).isoformat(),
        'plan_sha256':sha(a.plan),'source_sha256':plan['source_sha256'],'initial_safety':initial,'initial_telemetry':initial_telemetry,
        'windows':{f'chunk-{i:02d}':{'start_utc':(start+timedelta(seconds=offsets[i])).isoformat(),
            'end_utc':(start+timedelta(seconds=offsets[i+1])).isoformat()} for i in range(len(offsets)-1)},
        'private_fault_schedule':{f['action_id']:{'start_utc':(start+timedelta(seconds=f['offset_seconds'])).isoformat(),
            'end_utc':(start+timedelta(seconds=f['offset_seconds']+f['duration_seconds'])).isoformat()} for f in plan['actions']}}
    save(out/'schedule.json',schedule)
    commands={
        'state-machine':['/usr/bin/python3','-B',str(PROD/'scripts/pilot_workload.py'),'--machine',str(MACHINE),'--profiles',str(PROFILES),
            '--people',str(PEOPLE),'--profile','MEDIUM','--seed',str(plan['seed']),'--duration',str(duration),
            '--start-utc',start.isoformat(),'--base-url',BASE,'--output',str(out/'state-machine'),'--allow-errors'],
        'controls':['/usr/bin/python3','-B',str(PROD/'scripts/pilot_controls.py'),'--plan',str(a.plan),'--seed',str(plan['seed']),
            '--duration',str(duration),'--start-utc',start.isoformat(),'--base-url',BASE,'--output',str(out/'controls')]}
    processes={};handles=[];spawned=set()
    def launch(name,cmd):
        stdout=(out/(name+'.stdout')).open('x');stderr=(out/(name+'.stderr')).open('x');handles.extend((stdout,stderr))
        processes[name]=subprocess.Popen(cmd,stdout=stdout,stderr=stderr)
    def chunk(name,w):
        base=out/'capture'/name;first=base/'captures/during';middle=base/'trace-completion/during'
        launch(name+'-first',['/usr/bin/python3','-B',str(OLD/'capture_live_c2.py'),'--phase','during',
            '--start-utc',w['start_utc'],'--end-utc',w['end_utc'],'--trace-source','JAEGER_OPENSEARCH_STORAGE',
            '--wait-until-end','--output',str(first)])
        launch(name+'-completion',['/usr/bin/python3','-B',str(CG/'schedule_trace_completion_v2.py'),
            '--phase','during','--start',w['start_utc'],'--end',w['end_utc'],'--first-capture',str(first),'--output',str(middle)])
        launch(name+'-audit',['/usr/bin/python3','-B',str(CG/'schedule_trace_v2.py'),'--phase','during',
            '--start',w['start_utc'],'--end',w['end_utc'],'--first',str(first),'--plus60',str(middle),
            '--output',str(base/'trace-final/during'),'--audit-output',str(base/'trace-late-audit/during')])
        launch(name+'-quality',['/usr/bin/python3','-B',str(PROD/'scripts/pilot_chunk_audit.py'),'--chunk',name])
    result={'id':plan['id'],'status':'CASE_OBSERVATION_INVALID','faults_injected_by_this_program':0}
    for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):
        signal.signal(sig,lambda n,f:(_ for _ in ()).throw(SystemExit(128+n)))
    try:
        # Prestart checks are private control records, never formal capture rows.
        next_precheck=time.monotonic()+15
        while time.time()<start.timestamp()-3:
            if time.monotonic()>=next_precheck:
                check_started=now();row=safety(plan['targets_and_siblings']);row['collector']=telemetry_state(plan['collector_uid'])
                if row['collector']['refused']!=initial_telemetry['refused']:raise RuntimeError('Prestart receiver refusal')
                publish(CONTROL,plan,sha(a.plan),sha(__file__),sequence,check_started,{'environment':row,'collector':row['collector']});sequence+=1
                next_precheck=time.monotonic()+15
            time.sleep(.1)
        receipt=json.loads((CONTROL/'ready-receipt.json').read_text())
        if receipt['run_id']!=RUN_ID or receipt['run_token']!=plan['run_token'] or receipt['plan_sha256']!=sha(a.plan) or datetime.fromisoformat(receipt['at_utc']).timestamp()>=start.timestamp():raise RuntimeError('No timely matching READY receipt')
        for name,cmd in commands.items():launch(name,cmd)
        for side in ('start','end'):
            launch('private-boundary-'+side,['/usr/bin/python3','-B',str(PROD/'scripts/pilot_boundary_guard.py'),'--side',side])
        with (out/'safety.jsonl').open('x') as stream:
            tick=start.timestamp()
            while tick<=start.timestamp()+duration+480:
                time.sleep(max(0,tick-time.time()))
                if time.time()-tick>5:raise RuntimeError('Safety monitor deadline missed')
                check_started=now();row=safety(plan['targets_and_siblings']);row['collector']=telemetry_state(plan['collector_uid']);row['storage_budget']=runtime_check(RUN,CONTROL);stream.write(json.dumps(row)+'\n');stream.flush()
                publish(CONTROL,plan,sha(a.plan),sha(__file__),sequence,check_started,{'environment':row,'collector':row['collector']});sequence+=1
                if row['collector']['refused']!=initial_telemetry['refused']:raise RuntimeError('Collector refused-counter changed')
                for name,w in schedule['windows'].items():
                    if time.time()>datetime.fromisoformat(w['end_utc']).timestamp()+420 and not (out/'capture'/name/'quality.json').exists():raise RuntimeError('Mature chunk audit overdue')
                if any((out/'controls'/(k+'-failure.json')).exists() for k in ('image_asset','cart_view')):raise RuntimeError('Independent ordinary business control failed')
                if any(c.poll() not in (None,0) for c in processes.values()):raise RuntimeError('Load/capture process failed')
                for name,w in schedule['windows'].items():
                    if name not in spawned and datetime.fromisoformat(w['start_utc']).timestamp()<=tick:
                        chunk(name,w);spawned.add(name)
                if len(spawned)==len(offsets)-1 and all(c.poll() is not None for c in processes.values()):break
                tick+=15
        result['exit_codes']={n:c.wait(timeout=15) for n,c in processes.items()}
        if len(spawned)!=len(offsets)-1 or any(result['exit_codes'].values()):raise RuntimeError('Full-hour collection incomplete')
        if any(sha(path)!=digest for path,digest in plan['source_sha256'].items()):raise RuntimeError('Sources changed')
        result['status']='CASE_OBSERVATION_COMPLETE_PENDING_DATA_QUALITY'
    except BaseException as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        for c in processes.values():
            if c.poll() is None:
                c.terminate()
                try:c.wait(timeout=5)
                except subprocess.TimeoutExpired:c.kill()
        for f in handles:f.close()
        result['completed_utc']=now();save(out/'observation-result.json',result);print(json.dumps(result),flush=True)
    if result['status']!='CASE_OBSERVATION_COMPLETE_PENDING_DATA_QUALITY':raise SystemExit(1)

if __name__=='__main__':main()
