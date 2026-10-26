#!/usr/bin/env python3
"""Coordinate one predeclared bounded fault with paired ordinary workloads."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

import yaml


ROOT='/root/cross-layer-observability-control-rehearsal'
MASTER=('192.168.53.52','/tmp/stage4f-formal-ssh')
WORKERS={'k8s-worker1':('192.168.53.53','/tmp/oc-worker1-ssh'),
         'k8s-worker2':('192.168.53.54','/tmp/oc-worker2-ssh'),
         'k8s-worker3':('192.168.53.55','/tmp/oc-worker3-ssh')}


def ssh(host,*command,timeout=30):
    ip,socket=MASTER if host=='master' else WORKERS[host]
    return subprocess.run(['ssh','-o','BatchMode=yes','-o','ControlPath='+socket,
                           'root@'+ip,*command],capture_output=True,text=True,timeout=timeout)


def copy_remote_to_local(host,remote_path,local_path,recursive=False):
    ip,socket=MASTER if host=='master' else WORKERS[host]
    cmd=['scp','-q','-o','ControlPath='+socket]
    if recursive:cmd.append('-r')
    cmd.extend(['root@'+ip+':'+remote_path,str(local_path)])
    result=subprocess.run(cmd,capture_output=True,text=True,timeout=45)
    if result.returncode:raise RuntimeError('Copy from '+host+' failed: '+result.stderr[-400:])


def copy_local_to_worker(local_path,host,remote_path):
    ip,socket=WORKERS[host]
    result=subprocess.run(['scp','-q','-o','ControlPath='+socket,str(local_path),
        'root@'+ip+':'+remote_path],capture_output=True,text=True,timeout=45)
    if result.returncode:raise RuntimeError('Copy to '+host+' failed: '+result.stderr[-400:])


def wait_until(at):
    delay=at.timestamp()-time.time()
    if delay>0:time.sleep(delay)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--experiment-id',required=True)
    parser.add_argument('--health',required=True)
    parser.add_argument('--identity-audit',required=True)
    parser.add_argument('--control-analysis',required=True)
    parser.add_argument('--route-proof')
    parser.add_argument('--design',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    design=yaml.safe_load(args.design.read_text())
    specs=[row for row in design['experiments'] if row['id']==args.experiment_id]
    if len(specs)!=1:raise ValueError('Experiment ID not predeclared')
    spec=specs[0]
    seed=spec['seed']
    family=spec['family']
    adapter_plan=f'{ROOT}/design/fault-adapters.yaml'
    remote_design=f'{ROOT}/design/rehearsal-plan-v2.yaml'
    preflight=f'{ROOT}/attempts/{args.experiment_id}/preflight.json'
    remote_hash=ssh('master','sha256sum',remote_design)
    local_hash=hashlib.sha256(args.design.read_bytes()).hexdigest()
    if remote_hash.returncode or remote_hash.stdout.split()[0]!=local_hash:
        raise ValueError('Local and Master pre-fault design differ')
    build=['python3',f'{ROOT}/scripts/build_preflight.py','--design',remote_design,
        '--adapters',adapter_plan,'--health',args.health,
        '--identity-audit',args.identity_audit,'--control-analysis',args.control_analysis,
        '--experiment-id',args.experiment_id,'--output',preflight]
    if args.route_proof:build.extend(['--route-proof',args.route_proof])
    created=ssh('master',*build)
    (args.output/'preflight-command.json').write_text(json.dumps({
        'returncode':created.returncode,'stdout':created.stdout,'stderr':created.stderr},indent=2)+'\n')
    if created.returncode:raise RuntimeError('Preflight failed: '+created.stderr[-500:])
    copy_remote_to_local('master',preflight,args.output/'preflight.json')
    pre=json.loads((args.output/'preflight.json').read_text())
    source=pre['source_node'] if 'source_node' in pre else pre['targets'][0]['node']
    destination=pre.get('destination_node')
    is_network=family!='D06_TYPE_SINGLE_POD_PAUSE'
    script='network_fault.py' if is_network else 'host_fault.py'
    mkdir=ssh(source,'mkdir','-p',f'{ROOT}/scripts',f'{ROOT}/design',
              f'{ROOT}/attempts/{args.experiment_id}',f'{ROOT}/runtime')
    if mkdir.returncode:raise RuntimeError('Source Worker staging failed')
    for remote in (f'{ROOT}/scripts/{script}',adapter_plan,preflight):
        local=args.output/Path(remote).name
        if remote==preflight:local=args.output/'preflight.json'
        else:copy_remote_to_local('master',remote,local)
        copy_local_to_worker(local,source,remote)
    if is_network:
        remote=f'{ROOT}/scripts/probe_network.py'
        local=args.output/'probe_network.py'
        copy_remote_to_local('master',remote,local)
        copy_local_to_worker(local,source,remote)
    check=ssh(source,'python3',f'{ROOT}/scripts/{script}','precheck',
        '--plan',adapter_plan,'--preflight',preflight,'--experiment-id',args.experiment_id)
    (args.output/'adapter-precheck.json').write_text(json.dumps({
        'returncode':check.returncode,'stdout':check.stdout,'stderr':check.stderr},indent=2)+'\n')
    if check.returncode:raise RuntimeError('Fault adapter precheck failed: '+check.stderr[-500:])
    receiver=None
    unit='oc-iperf-'+args.experiment_id.lower()+'-'+str(destination)
    pair_proc=None
    fault_proc=None
    probes=[]
    state=f'{ROOT}/runtime/{args.experiment_id}-{source}.json'
    try:
        if is_network:
            listener=ssh(destination,'ss','-ltn','sport','=',':5201')
            if listener.returncode or ':5201' in listener.stdout:
                raise RuntimeError('Path probe port 5201 already occupied')
            server=ssh(destination,'systemd-run','--quiet','--unit='+unit,
                '/usr/bin/timeout','370','/usr/bin/iperf3','-s','-B',
                pre['destination_node_ip'],'-p','5201')
            if server.returncode:raise RuntimeError('Cannot start bounded iperf receiver')
            receiver=True
            time.sleep(1)
            if ':5201' not in ssh(destination,'ss','-ltn','sport','=',':5201').stdout:
                raise RuntimeError('Iperf receiver did not bind')
        run_dir=f'{ROOT}/attempts/{args.experiment_id}/load_pair'
        command=['ssh','-o','BatchMode=yes','-o','ControlPath='+MASTER[1],
            'root@'+MASTER[0],'python3',f'{ROOT}/scripts/run_load_pair.py',
            '--plan',remote_design,'--health',args.health,'--run-id',args.experiment_id,
            '--seed',str(seed),'--output',run_dir,'--allow-errors']
        pair_out=(args.output/'load-pair.stdout').open('x')
        pair_err=(args.output/'load-pair.stderr').open('x')
        pair_proc=subprocess.Popen(command,stdout=pair_out,stderr=pair_err)
        for _ in range(20):
            if pair_proc.poll() is not None:raise RuntimeError('Load pair exited before schedule')
            schedule=ssh('master','cat',run_dir+'/schedule.json')
            if schedule.returncode==0:break
            time.sleep(1)
        else:raise RuntimeError('Load pair schedule missing')
        schedule=json.loads(schedule.stdout)
        start=datetime.fromisoformat(schedule['scheduled_start_utc'])
        fault_at=start+timedelta(seconds=120)
        (args.output/'schedule.json').write_text(json.dumps(schedule,indent=2)+'\n')
        if is_network:
            wait_until(start+timedelta(seconds=5))
            for phase,at,samples in [('before',start+timedelta(seconds=10),11),
                                      ('during',fault_at,6),
                                      ('after',start+timedelta(seconds=180),12)]:
                ip,socket=WORKERS[source]
                cmd=['ssh','-o','BatchMode=yes','-o','ControlPath='+socket,'root@'+ip,
                    'python3',f'{ROOT}/scripts/probe_network.py','--path',pre['path'],
                    '--source-ip',pre['source_node_ip'],
                    '--destination-ip',pre['destination_node_ip'],
                    '--start-utc',at.isoformat(),'--samples',str(samples),
                    '--interval','10','--output',f'{ROOT}/attempts/{args.experiment_id}/probe-{phase}']
                out=(args.output/f'probe-{phase}.stdout').open('x')
                err=(args.output/f'probe-{phase}.stderr').open('x')
                probes.append((phase,subprocess.Popen(cmd,stdout=out,stderr=err),out,err))
        wait_until(start+timedelta(seconds=30))
        ip,socket=WORKERS[source]
        cmd=['ssh','-o','BatchMode=yes','-o','ControlPath='+socket,'root@'+ip,
            'python3',f'{ROOT}/scripts/{script}','run','--plan',adapter_plan,
            '--preflight',preflight,'--experiment-id',args.experiment_id,
            '--start-utc',fault_at.isoformat(),'--state-file',state]
        fault_out=(args.output/'fault.stdout').open('x')
        fault_err=(args.output/'fault.stderr').open('x')
        fault_proc=subprocess.Popen(cmd,stdout=fault_out,stderr=fault_err)
        fault_code=fault_proc.wait(timeout=240)
        fault_out.close();fault_err.close()
        errors=[]
        if fault_code:errors.append('fault_exit_'+str(fault_code))
        stored=ssh(source,'cat',state)
        if stored.returncode==0:
            (args.output/'state.json').write_text(stored.stdout)
            if json.loads(stored.stdout).get('status')!='CLEANED':errors.append('fault_not_cleaned')
        else:errors.append('state_missing')
        for phase,process,out,err in probes:
            code=process.wait(timeout=330)
            out.close();err.close()
            if code:errors.append('probe_'+phase+'_exit_'+str(code))
            try:
                copy_remote_to_local(source,f'{ROOT}/attempts/{args.experiment_id}/probe-{phase}',
                                     args.output/f'probe-{phase}',recursive=True)
            except Exception as exc:
                errors.append('probe_'+phase+'_archive_'+str(exc))
        pair_code=pair_proc.wait(timeout=400)
        pair_out.close();pair_err.close()
        if pair_code:errors.append('load_pair_exit_'+str(pair_code))
        result={'experiment_id':args.experiment_id,'family':family,
            'seed':seed,'status':'REHEARSAL_COMPLETE' if not errors else 'REHEARSAL_INCOMPLETE',
            'fault_start_utc':fault_at.isoformat(),
            'fault_end_utc':(fault_at+timedelta(seconds=60)).isoformat(),
            'source_node':source,'destination_node':destination,
            'errors':errors,'gt_read':False,'formal_case_created':False}
        (args.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result),flush=True)
        if errors:raise RuntimeError('Rehearsal incomplete')
    finally:
        if fault_proc and fault_proc.poll() is None:
            fault_proc.terminate()
            try:fault_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:fault_proc.kill()
        current=ssh(source,'cat',state)
        if current.returncode==0 and json.loads(current.stdout).get('status')!='CLEANED':
            ssh(source,'python3',f'{ROOT}/scripts/{script}','cleanup','--state-file',state)
        for _,process,out,err in probes:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=15)
                except subprocess.TimeoutExpired:process.kill()
            if not out.closed:out.close()
            if not err.closed:err.close()
        if pair_proc and pair_proc.poll() is None:
            pair_proc.terminate()
            try:pair_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:pair_proc.kill()
        if receiver:
            ssh(destination,'systemctl','stop',unit+'.service')
            ssh(destination,'systemctl','reset-failed',unit+'.service')


if __name__=='__main__':main()
