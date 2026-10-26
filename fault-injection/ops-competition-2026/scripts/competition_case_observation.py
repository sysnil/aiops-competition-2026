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
from competition_observation import (ROOT,DATA,OLD,CG,STATE,CONTROLS,MACHINE,
    PROFILES,PEOPLE,BASE,storage,safety,save,sha,now)

def main():
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--health',type=Path,required=True);a=p.parse_args()
    plan=json.loads(a.plan.read_text());health=json.loads(a.health.read_text())
    telemetry=ROOT/'qualification/production-telemetry-readiness.json'
    if json.loads(telemetry.read_text()).get('status')!='COMPETITION_PRODUCTION_TELEMETRY_PASS' or sha(telemetry)!=plan.get('production_telemetry_proof_sha256'):
        raise RuntimeError('Production capture readiness blocked or proof changed')
    if plan['id']!='SF-PILOT-01' or plan['duration_seconds']!=3600:raise RuntimeError('Only the one registered hour is allowed')
    if health['status']!='COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING' or health['current_environment_blockers']:raise RuntimeError('Actual safety failed')
    if not 0<=(datetime.now(timezone.utc)-datetime.fromisoformat(health['at_utc'])).total_seconds()<=180:raise RuntimeError('Safety stale')
    for path,digest in plan['source_sha256'].items():
        if sha(path)!=digest:raise RuntimeError('Registered source changed')
    for proof in plan['prerequisites']:
        if sha(proof['file'])!=proof['sha256'] or json.loads(Path(proof['file']).read_text()).get('qualified') is not True:raise RuntimeError('Mechanism not qualified')
    if {r['id'] for r in plan['prerequisites']}!={'COMP-SF01-SERVICE-300','COMP-SF01-POD-300'}:raise RuntimeError('Both mechanisms required')
    storage();initial=safety(plan['targets_and_siblings']);out=DATA/'runs/SF-PILOT-01'
    if out.exists():raise RuntimeError('Never overwrite or retry a case')
    out.mkdir(parents=True,mode=0o700)
    start=datetime.now(timezone.utc)+timedelta(seconds=45)
    schedule={'id':plan['id'],'start_utc':start.isoformat(),'end_utc':(start+timedelta(hours=1)).isoformat(),
        'plan_sha256':sha(a.plan),'source_sha256':plan['source_sha256'],'initial_safety':initial,
        'windows':{f'chunk-{i:02d}':{'start_utc':(start+timedelta(seconds=i*600)).isoformat(),
            'end_utc':(start+timedelta(seconds=(i+1)*600)).isoformat()} for i in range(6)},
        'private_fault_schedule':{f['id']:{'start_utc':(start+timedelta(seconds=f['offset_seconds'])).isoformat(),
            'end_utc':(start+timedelta(seconds=f['offset_seconds']+300)).isoformat()} for f in plan['faults']}}
    save(out/'schedule.json',schedule)
    commands={
        'state-machine':['/usr/bin/python3','-B',str(STATE),'--machine',str(MACHINE),'--profiles',str(PROFILES),
            '--people',str(PEOPLE),'--profile','MEDIUM','--seed',str(plan['seed']),'--duration','3600',
            '--start-utc',start.isoformat(),'--base-url',BASE,'--output',str(out/'state-machine'),'--allow-errors'],
        'controls':['/usr/bin/python3','-B',str(CONTROLS),'--plan',str(a.plan),'--seed',str(plan['seed']),
            '--duration','3600','--start-utc',start.isoformat(),'--base-url',BASE,'--output',str(out/'controls')]}
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
    result={'id':plan['id'],'status':'CASE_OBSERVATION_INVALID','faults_injected_by_this_program':0}
    for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):
        signal.signal(sig,lambda n,f:(_ for _ in ()).throw(SystemExit(128+n)))
    try:
        for name,cmd in commands.items():launch(name,cmd)
        with (out/'safety.jsonl').open('x') as stream:
            tick=start.timestamp()
            while tick<=start.timestamp()+3900:
                time.sleep(max(0,tick-time.time()))
                if time.time()-tick>5:raise RuntimeError('Safety monitor deadline missed')
                row=safety(plan['targets_and_siblings']);stream.write(json.dumps(row)+'\n');stream.flush()
                if (out/'controls/image-control-failure.json').exists():raise RuntimeError('Independent Image control failed')
                if any(c.poll() not in (None,0) for c in processes.values()):raise RuntimeError('Load/capture process failed')
                for name,w in schedule['windows'].items():
                    if name not in spawned and datetime.fromisoformat(w['start_utc']).timestamp()<=tick:
                        chunk(name,w);spawned.add(name)
                if len(spawned)==6 and all(c.poll() is not None for c in processes.values()):break
                tick+=15
        result['exit_codes']={n:c.wait(timeout=15) for n,c in processes.items()}
        if len(spawned)!=6 or any(result['exit_codes'].values()):raise RuntimeError('Full-hour collection incomplete')
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
