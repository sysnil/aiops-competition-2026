#!/usr/bin/env python3
"""Run one independent, bounded host rehearsal with unchanged 5-minute workload."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import time


ROOT='/root/cross-layer-workload-generation'
MASTER=('192.168.53.52','/tmp/stage4f-formal-ssh')
WORKERS={'k8s-worker1':('192.168.53.53','/tmp/phase2-worker1-ssh'),
         'k8s-worker2':('192.168.53.54','/tmp/phase2-worker2-ssh'),
         'k8s-worker3':('192.168.53.55','/tmp/phase2-worker3-ssh')}


def ssh(host,*command,timeout=30):
    ip,socket=MASTER if host=='master' else WORKERS[host]
    return subprocess.run(['ssh','-o','BatchMode=yes','-o','ControlPath='+socket,
                           'root@'+ip,*command],capture_output=True,text=True,timeout=timeout)


def scp(source,destination):
    result=subprocess.run(['scp','-q','-o','BatchMode=yes',source,destination],
                          capture_output=True,text=True,timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr[-500:])


def remote_file(host,path):
    result=ssh(host,'cat',path)
    if result.returncode:
        raise RuntimeError(result.stderr[-500:])
    return result.stdout


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--experiment-id',choices=['WG-SERVICE-PULSE-R2','WG-POD-PULSE'],required=True)
    parser.add_argument('--health',required=True)
    parser.add_argument('--seed',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    attempt=f'{ROOT}/attempts/{args.experiment_id}'
    plan=f'{ROOT}/design/fault-adaptation-plan.yaml'
    preflight=f'{attempt}/preflight.json'
    result=ssh('master','python3',f'{ROOT}/scripts/build_preflight.py',
        '--plan',plan,'--health',args.health,'--experiment-id',args.experiment_id,
        '--output',preflight)
    (args.output/'preflight-command.json').write_text(json.dumps(
        {'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr},indent=2)+'\n')
    if result.returncode:
        raise RuntimeError('Preflight failed: '+result.stderr[-500:])
    data=json.loads(remote_file('master',preflight))
    (args.output/'preflight.json').write_text(json.dumps(data,indent=2)+'\n')
    nodes=sorted({row['node'] for row in data['targets']})
    for node in nodes:
        ip,socket=WORKERS[node]
        mkdir=ssh(node,'mkdir','-p',f'{ROOT}/scripts',f'{ROOT}/design',f'{attempt}',f'{ROOT}/runtime')
        if mkdir.returncode:
            raise RuntimeError('Cannot prepare worker runtime')
        for local,remote in [(f'{ROOT}/scripts/host_fault.py',f'{ROOT}/scripts/host_fault.py'),
                             (plan,plan),(preflight,preflight)]:
            result=subprocess.run(['scp','-q','-o','ControlPath='+MASTER[1],
                'root@'+MASTER[0]+':'+local,str(args.output/local.rsplit('/',1)[-1])],
                capture_output=True,text=True,timeout=30)
            if result.returncode:
                raise RuntimeError('Cannot stage isolated worker input: '+result.stderr[-300:])
            result=subprocess.run(['scp','-q','-o','ControlPath='+socket,
                str(args.output/local.rsplit('/',1)[-1]),'root@'+ip+':'+remote],
                capture_output=True,text=True,timeout=30)
            if result.returncode:
                raise RuntimeError('Cannot stage worker input: '+result.stderr[-300:])
        check=ssh(node,'python3',f'{ROOT}/scripts/host_fault.py','precheck',
                  '--plan',plan,'--preflight',preflight,'--experiment-id',args.experiment_id)
        (args.output/f'precheck-{node}.json').write_text(json.dumps(
            {'exit_code':check.returncode,'stdout':check.stdout,'stderr':check.stderr},indent=2)+'\n')
        if check.returncode:
            raise RuntimeError('Host fault precheck failed on '+node)
    load=f'{attempt}/load'
    load_cmd=['ssh','-o','BatchMode=yes','-o','ControlPath='+MASTER[1],
        'root@'+MASTER[0], 'python3',f'{ROOT}/scripts/workload-generator.py',
        '--machine',f'{ROOT}/design/workload-state-machine-v2.yaml',
        '--profiles',f'{ROOT}/design/load-profiles.yaml',
        '--people',f'{ROOT}/design/people.json','--profile','MEDIUM',
        '--seed',str(args.seed),'--duration','300','--allow-errors',
        '--base-url','http://10.104.177.128:8080',
        '--output',load]
    with (args.output/'load.stdout').open('x') as output, (args.output/'load.stderr').open('x') as error:
        load_proc=subprocess.Popen(load_cmd,stdout=output,stderr=error)
        try:
            for _ in range(15):
                if load_proc.poll() is not None:
                    raise RuntimeError('Load exited before first request')
                probe=ssh('master','cat',load+'/input-manifest.json')
                if probe.returncode==0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError('Load input manifest did not appear')
            started=datetime.fromisoformat(json.loads(probe.stdout)['started_utc'])
            fault_start=started+timedelta(seconds=120)
            fault_end=fault_start+timedelta(seconds=60)
            run_manifest={'experiment_id':args.experiment_id,'load_start_utc':started.isoformat(),
                'fault_start_utc':fault_start.isoformat(), 'fault_end_utc':fault_end.isoformat(),
                'planned_load_end_utc':(started+timedelta(seconds=300)).isoformat(),
                'seed':args.seed,'target_nodes':nodes,'fault_duration_seconds':60,
                'formal_stage5_case':False}
            (args.output/'schedule.json').write_text(json.dumps(run_manifest,indent=2)+'\n')
            probes=[]
            probe_interval=2 if args.experiment_id=='WG-POD-PULSE' else 5
            for phase,at,duration in [('before',started+timedelta(seconds=20),100),('during',fault_start,60),
                                      ('after',fault_end,120)]:
                cmd=['ssh','-o','BatchMode=yes','-o','ControlPath='+MASTER[1],
                    'root@'+MASTER[0],'python3',f'{ROOT}/scripts/probe_frontend.py',
                    '--preflight',preflight,'--start-utc',at.isoformat(),
                    '--duration',str(duration),'--interval',str(probe_interval),
                    '--output',f'{attempt}/direct-probe-{phase}']
                out=(args.output/f'probe-{phase}.stdout').open('x')
                err=(args.output/f'probe-{phase}.stderr').open('x')
                probes.append((phase,subprocess.Popen(cmd,stdout=out,stderr=err),out,err))
            faults=[]
            for node in nodes:
                ip,socket=WORKERS[node]
                state=f'{ROOT}/runtime/{args.experiment_id}-{node}.json'
                cmd=['ssh','-o','BatchMode=yes','-o','ControlPath='+socket,'root@'+ip,
                    'python3',f'{ROOT}/scripts/host_fault.py','run','--plan',plan,
                    '--preflight',preflight,'--experiment-id',args.experiment_id,
                    '--start-utc',fault_start.isoformat(),'--state-file',state]
                out=(args.output/f'fault-{node}.stdout').open('x')
                err=(args.output/f'fault-{node}.stderr').open('x')
                faults.append((node,state,subprocess.Popen(cmd,stdout=out,stderr=err),out,err))
            failures=[]
            for node,state,process,out,err in faults:
                code=process.wait(timeout=250)
                out.close();err.close()
                if code:
                    failures.append(f'{node}:exit={code}')
                state_result=ssh(node,'cat',state)
                if state_result.returncode==0:
                    (args.output/f'state-{node}.json').write_text(state_result.stdout)
                    if json.loads(state_result.stdout).get('status')!='CLEANED':
                        failures.append(f'{node}:not-cleaned')
                else:
                    failures.append(f'{node}:state-missing')
            for phase,process,out,err in probes:
                code=process.wait(timeout=330)
                out.close();err.close()
                if code:
                    failures.append(f'probe-{phase}:exit={code}')
            if load_proc.wait(timeout=330):
                failures.append('load:failed')
            run_manifest['status']='COMPLETE' if not failures else 'INCOMPLETE'
            run_manifest['failures']=failures
            (args.output/'result.json').write_text(json.dumps(run_manifest,indent=2)+'\n')
            if failures:
                raise RuntimeError('Rehearsal incomplete: '+','.join(failures))
            print(json.dumps({'status':'REHEARSAL_COMPLETE', 'experiment_id':args.experiment_id,
                              'target_nodes':nodes}))
        finally:
            for node,state,process,out,err in locals().get('faults',[]):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        pass
                current=ssh(node,'cat',state)
                if current.returncode==0 and json.loads(current.stdout).get('status')!='CLEANED':
                    ssh(node,'python3',f'{ROOT}/scripts/host_fault.py','cleanup',
                        '--state-file',state)
                if not out.closed: out.close()
                if not err.closed: err.close()
            for phase,process,out,err in locals().get('probes',[]):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        pass
                if not out.closed: out.close()
                if not err.closed: err.close()
            if load_proc.poll() is None:
                load_proc.terminate()
                try:
                    load_proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    pass


if __name__=='__main__':
    main()
