#!/usr/bin/env python3
"""Bounded path rehearsal with public route proof, independent controls and probe cleanup."""

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import time


ROOT='/root/cross-layer-workload-generation'
HOSTS={'master':('192.168.53.52','/tmp/stage4f-formal-ssh'),
       'k8s-worker1':('192.168.53.53','/tmp/phase2-worker1-ssh'),
       'k8s-worker2':('192.168.53.54','/tmp/phase2-worker2-ssh'),
       'k8s-worker3':('192.168.53.55','/tmp/phase2-worker3-ssh')}


def ssh(host,*args,timeout=30):
    ip,socket=HOSTS[host]
    return subprocess.run(['ssh','-o','BatchMode=yes','-o','ControlPath='+socket,
                           'root@'+ip,*args],capture_output=True,text=True,timeout=timeout)


def copy_from_master(remote_path,local_path):
    result=subprocess.run(['scp','-q','-o','ControlPath='+HOSTS['master'][1],
        'root@'+HOSTS['master'][0]+':'+remote_path,str(local_path)],
        capture_output=True,text=True,timeout=30)
    if result.returncode: raise RuntimeError('Master staging failed: '+result.stderr[-300:])


def copy_to_worker(local_path,host,remote_path):
    ip,socket=HOSTS[host]
    result=subprocess.run(['scp','-q','-o','ControlPath='+socket,str(local_path),
                           'root@'+ip+':'+remote_path],capture_output=True,text=True,timeout=30)
    if result.returncode: raise RuntimeError('Worker staging failed: '+result.stderr[-300:])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--experiment-id',choices=['WG-NET-LOSS','WG-NET-BANDWIDTH'],required=True)
    parser.add_argument('--health',required=True)
    parser.add_argument('--route-proof',required=True)
    parser.add_argument('--control-proof',required=True)
    parser.add_argument('--seed',required=True,type=int)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    attempt=f'{ROOT}/attempts/{args.experiment_id}'
    plan=f'{ROOT}/design/fault-adaptation-plan.yaml'
    preflight=f'{attempt}/preflight.json'
    build=ssh('master','python3',f'{ROOT}/scripts/build-network-preflight.py',
        '--plan',plan,'--health',args.health,'--route-proof',args.route_proof,
        '--control-proof',args.control_proof,'--experiment-id',args.experiment_id,
        '--output',preflight)
    (args.output/'preflight-command.json').write_text(json.dumps(
        {'exit_code':build.returncode,'stdout':build.stdout,'stderr':build.stderr},indent=2)+'\n')
    if build.returncode: raise RuntimeError('Network preflight failed: '+build.stderr[-500:])
    preflight_local=args.output/'preflight.json'
    copy_from_master(preflight,preflight_local)
    data=json.loads(preflight_local.read_text())
    source,destination=data['source_node'],data['destination_node']
    mkdir=ssh(source,'mkdir','-p',f'{ROOT}/scripts',f'{ROOT}/design',attempt,f'{ROOT}/runtime')
    if mkdir.returncode: raise RuntimeError('Cannot prepare source worker runtime')
    for path in (f'{ROOT}/scripts/network_fault.py',f'{ROOT}/scripts/probe_network.py',plan):
        local=args.output/Path(path).name
        copy_from_master(path,local)
        copy_to_worker(local,source,path)
    copy_to_worker(preflight_local,source,preflight)
    check=ssh(source,'python3',f'{ROOT}/scripts/network_fault.py','precheck',
        '--plan',plan,'--preflight',preflight,'--experiment-id',args.experiment_id)
    (args.output/'precheck.json').write_text(json.dumps(
        {'exit_code':check.returncode,'stdout':check.stdout,'stderr':check.stderr},indent=2)+'\n')
    if check.returncode: raise RuntimeError('Network adapter precheck failed: '+check.stderr[-500:])
    unit='wg-iperf-'+args.experiment_id.lower()+'-'+destination
    receiver_started=False
    load_proc=None
    faults=[]; probes=[]
    try:
        listening=ssh(destination,'ss','-ltn','sport','=',':5201')
        if listening.returncode or ':5201' in listening.stdout:
            raise RuntimeError('Destination port 5201 already occupied')
        server=ssh(destination,'systemd-run','--quiet','--unit='+unit,
            '/usr/bin/timeout','370','/usr/bin/iperf3','-s','-B',data['destination_node_ip'],
            '-p','5201')
        if server.returncode: raise RuntimeError('Cannot start bounded iperf receiver: '+server.stderr[-500:])
        receiver_started=True
        time.sleep(1)
        listening=ssh(destination,'ss','-ltn','sport','=',':5201')
        if ':5201' not in listening.stdout: raise RuntimeError('Iperf receiver did not bind')
        load=f'{attempt}/load'
        cmd=['ssh','-o','BatchMode=yes','-o','ControlPath='+HOSTS['master'][1],
            'root@'+HOSTS['master'][0],'python3',f'{ROOT}/scripts/workload-generator.py',
            '--machine',f'{ROOT}/design/workload-state-machine-v2.yaml',
            '--profiles',f'{ROOT}/design/load-profiles.yaml',
            '--people',f'{ROOT}/design/people.json','--profile','MEDIUM',
            '--seed',str(args.seed),'--duration','300','--allow-errors',
            '--base-url','http://10.104.177.128:8080','--output',load]
        load_out=(args.output/'load.stdout').open('x')
        load_err=(args.output/'load.stderr').open('x')
        load_proc=subprocess.Popen(cmd,stdout=load_out,stderr=load_err)
        for _ in range(15):
            if load_proc.poll() is not None: raise RuntimeError('Load exited before start')
            result=ssh('master','cat',load+'/input-manifest.json')
            if result.returncode==0: break
            time.sleep(1)
        else: raise RuntimeError('No load input manifest')
        started=datetime.fromisoformat(json.loads(result.stdout)['started_utc'])
        fault_start=started+timedelta(seconds=120)
        fault_end=fault_start+timedelta(seconds=60)
        schedule={'experiment_id':args.experiment_id,'load_start_utc':started.isoformat(),
            'fault_start_utc':fault_start.isoformat(),'fault_end_utc':fault_end.isoformat(),
            'planned_load_end_utc':(started+timedelta(seconds=300)).isoformat(),
            'source_node':source,'destination_node':destination,'path':data['path'],
            'seed':args.seed,'probe_traffic_separate':True,'formal_stage5_case':False}
        (args.output/'schedule.json').write_text(json.dumps(schedule,indent=2)+'\n')
        for phase,at,samples in [('before',started+timedelta(seconds=10),11),
                                  ('during',fault_start,6),('after',fault_end,12)]:
            ip,socket=HOSTS[source]
            cmd=['ssh','-o','BatchMode=yes','-o','ControlPath='+socket,'root@'+ip,
                'python3',f'{ROOT}/scripts/probe_network.py','--path',data['path'],
                '--source-ip',data['source_node_ip'],
                '--destination-ip',data['destination_node_ip'],
                '--start-utc',at.isoformat(),'--samples',str(samples),
                '--interval','10','--output',f'{attempt}/probe-{phase}']
            out=(args.output/f'probe-{phase}.stdout').open('x')
            err=(args.output/f'probe-{phase}.stderr').open('x')
            probes.append((phase,subprocess.Popen(cmd,stdout=out,stderr=err),out,err))
        state=f'{ROOT}/runtime/{args.experiment_id}-{source}.json'
        ip,socket=HOSTS[source]
        cmd=['ssh','-o','BatchMode=yes','-o','ControlPath='+socket,'root@'+ip,
            'python3',f'{ROOT}/scripts/network_fault.py','run','--plan',plan,
            '--preflight',preflight,'--experiment-id',args.experiment_id,
            '--start-utc',fault_start.isoformat(),'--state-file',state]
        out=(args.output/'fault.stdout').open('x')
        err=(args.output/'fault.stderr').open('x')
        faults.append((subprocess.Popen(cmd,stdout=out,stderr=err),out,err,state))
        failures=[]
        for process,out,err,state in faults:
            code=process.wait(timeout=250)
            out.close();err.close()
            if code: failures.append('fault:exit='+str(code))
            current=ssh(source,'cat',state)
            if current.returncode==0:
                (args.output/'state.json').write_text(current.stdout)
                if json.loads(current.stdout).get('status')!='CLEANED':
                    failures.append('network-not-cleaned')
            else: failures.append('network-state-missing')
        for phase,process,out,err in probes:
            code=process.wait(timeout=330)
            out.close();err.close()
            if code: failures.append('probe-'+phase+':exit='+str(code))
            ip,socket=HOSTS[source]
            result=subprocess.run(['scp','-q','-r','-o','ControlPath='+socket,
                'root@'+ip+':'+f'{attempt}/probe-{phase}',
                str(args.output/f'probe-{phase}')],capture_output=True,text=True,timeout=30)
            if result.returncode: failures.append('probe-'+phase+':archive-failed')
        if load_proc.wait(timeout=330): failures.append('load:failed')
        schedule['status']='COMPLETE' if not failures else 'INCOMPLETE'
        schedule['failures']=failures
        (args.output/'result.json').write_text(json.dumps(schedule,indent=2)+'\n')
        if failures: raise RuntimeError('Rehearsal incomplete: '+','.join(failures))
        print(json.dumps({'status':'NETWORK_REHEARSAL_COMPLETE',
                          'experiment_id':args.experiment_id,'path':data['path']}))
    finally:
        for process,out,err,state in faults:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=15)
                except subprocess.TimeoutExpired: pass
            current=ssh(source,'cat',state)
            if current.returncode==0 and json.loads(current.stdout).get('status')!='CLEANED':
                ssh(source,'python3',f'{ROOT}/scripts/network_fault.py','cleanup',
                    '--state-file',state)
            if not out.closed: out.close()
            if not err.closed: err.close()
        for phase,process,out,err in probes:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=15)
                except subprocess.TimeoutExpired: pass
            if not out.closed: out.close()
            if not err.closed: err.close()
        if load_proc and load_proc.poll() is None:
            load_proc.terminate()
            try: load_proc.wait(timeout=15)
            except subprocess.TimeoutExpired: pass
        if receiver_started:
            ssh(destination,'systemctl','stop',unit+'.service')
            ssh(destination,'systemctl','reset-failed',unit+'.service')


if __name__=='__main__':
    main()
