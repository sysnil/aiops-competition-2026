#!/usr/bin/env python3
"""Guarded Checkout Pod UID rollout during a four-client no-fault test."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time


SERVICES=('checkout',)
ROOT=Path('/root/cross-layer-rehearsal-phase4')
PREFIX='k8s.pod.uid=$(WG_POD_UID),k8s.pod.name=$(WG_POD_NAME)'


def kube(*args,timeout=45):
    result=subprocess.run(['kubectl','-n','otel-demo',*args],capture_output=True,
                          text=True,timeout=timeout)
    if result.returncode:
        raise RuntimeError('kubectl failed: '+result.stderr[-1000:])
    return result.stdout


def current_deployment(service):
    return json.loads(kube('get','deployment',service,'-o','json'))


def env_of(deployment):
    containers=deployment['spec']['template']['spec']['containers']
    if len(containers)!=1:
        raise ValueError('Expected one application container')
    return containers[0]['env']


def original_from_backup(backup,service):
    path=backup/('deployment-'+service+'.json')
    data=json.loads(path.read_text())
    return data,env_of(data)


def enriched(original):
    names=[row['name'] for row in original]
    if (len(names)!=len(set(names)) or 'WG_POD_UID' in names or 'WG_POD_NAME' in names or
            names.count('OTEL_RESOURCE_ATTRIBUTES')!=1):
        raise ValueError('Unexpected existing Pod identity or duplicate environment variables')
    modified=[]
    for row in original:
        if row['name']=='OTEL_RESOURCE_ATTRIBUTES':
            value=row.get('value','')
            if 'k8s.pod.uid' in value or 'k8s.pod.name' in value:
                raise ValueError('Resource already has Pod identity')
            modified.append({**row,'value':value+','+PREFIX})
        else:
            modified.append(row)
    return [
        {'name':'WG_POD_UID','valueFrom':{'fieldRef':{'apiVersion':'v1','fieldPath':'metadata.uid'}}},
        {'name':'WG_POD_NAME','valueFrom':{'fieldRef':{'apiVersion':'v1','fieldPath':'metadata.name'}}},
        *modified]


def matching_ready_pods(all_pods,new_env):
    return [pod for pod in all_pods if pod['metadata'].get('deletionTimestamp') is None
            and pod['spec']['containers'][0].get('env')==new_env
            and pod['status'].get('phase')=='Running'
            and all(status.get('ready') for status in
                    pod['status'].get('containerStatuses',[]))]


def plan(backup,output):
    if output.exists(): raise ValueError('Refusing to replace rollout plan')
    entries={}
    for service in SERVICES:
        previous,original=original_from_backup(backup,service)
        live=current_deployment(service)
        if (live['metadata']['uid']!=previous['metadata']['uid'] or
                env_of(live)!=original or live['spec']['replicas']!=4 or
                live['status'].get('readyReplicas')!=4):
            raise ValueError(service+' changed since backup or is not four-replica Ready')
        entries[service]={'deployment_uid':live['metadata']['uid'],
                          'image':live['spec']['template']['spec']['containers'][0]['image'],
                          'original_env':original,'new_env':enriched(original),
                          'backup_sha256':hashlib.sha256((backup/('deployment-'+service+'.json')).read_bytes()).hexdigest()}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({'created_at_utc':datetime.now(timezone.utc).isoformat(),
        'method':'Downward API to OTEL_RESOURCE_ATTRIBUTES','services':entries},indent=2)+'\n')
    print(json.dumps({'status':'ROLLOUT_PLAN_READY','services':list(entries)}))


def patch_env(service,expected,new_env):
    live=current_deployment(service)
    if env_of(live)!=expected or live['spec']['replicas']!=4:
        raise ValueError(service+' environment changed outside this rollout')
    patch=[{'op':'test','path':'/metadata/resourceVersion',
            'value':live['metadata']['resourceVersion']},
           {'op':'replace','path':'/spec/template/spec/containers/0/env','value':new_env}]
    kube('patch','deployment',service,'--type=json','-p',json.dumps(patch),timeout=45)
    kube('rollout','status','deployment/'+service,'--timeout=240s',timeout=255)
    after=current_deployment(service)
    if (env_of(after)!=new_env or after['status'].get('readyReplicas')!=4 or
            after['status'].get('updatedReplicas')!=4):
        raise ValueError(service+' rollout did not reach four updated Ready Pods')
    pods=[]
    for _ in range(30):
        all_pods=json.loads(kube('get','pods','-l','opentelemetry.io/name='+service,
                                 '-o','json'))['items']
        pods=matching_ready_pods(all_pods,new_env)
        if len(pods)==4 and len({pod['metadata']['uid'] for pod in pods})==4:
            break
        time.sleep(2)
    else:
        raise ValueError(service+' did not converge to four Ready Pods with the expected env')
    return {'service':service,'at_utc':datetime.now(timezone.utc).isoformat(),
            'deployment_revision':after['metadata'].get('annotations',{}).get('deployment.kubernetes.io/revision'),
            'pod_identities':[{'name':pod['metadata']['name'],
                               'uid':pod['metadata']['uid'],
                               'ip':pod['status'].get('podIP'),
                               'node':pod['spec'].get('nodeName')}
                              for pod in pods]}


def change(service,mode,rollout_plan,output):
    if output.exists(): raise ValueError('Refusing to overwrite rollout record')
    data=json.loads(rollout_plan.read_text())['services'][service]
    expected,new=(data['original_env'],data['new_env']) if mode=='apply' else (
        data['new_env'],data['original_env'])
    try:
        result=patch_env(service,expected,new)
        result['status']='ROLLOUT_PASS' if mode=='apply' else 'ROLLBACK_PASS'
    except Exception as exc:
        result={'service':service,'status':'ROLLOUT_FAILED' if mode=='apply' else 'ROLLBACK_FAILED',
                'at_utc':datetime.now(timezone.utc).isoformat(),'error':str(exc)}
        if mode=='apply':
            try:
                result['automatic_rollback']=patch_env(service,new,data['original_env'])
            except Exception as rollback_error:
                result['automatic_rollback_error']=str(rollback_error)
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,indent=2)+'\n')
        raise
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'service':service,
                      'pod_uids':[row['uid'] for row in result['pod_identities']]}))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['plan','apply','rollback'])
    parser.add_argument('--backup',type=Path)
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--service',choices=SERVICES)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.mode=='plan':
        plan(args.backup,args.output)
    else:
        if not args.service or not args.plan: raise ValueError('Service and plan required')
        change(args.service,args.mode,args.plan,args.output)


if __name__=='__main__':main()
