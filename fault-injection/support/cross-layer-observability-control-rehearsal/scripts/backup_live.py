#!/usr/bin/env python3
"""Snapshot live telemetry configuration and target Deployments before edits."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


SERVICES=('frontend','ad','payment','recommendation')
OBJECTS=[('deployment',name) for name in SERVICES]+[
    ('deployment','otel-collector'),('configmap','otel-collector'),
    ('configmap','otel-cluster-collector'),('serviceaccount','otel-collector')]


def kube(*args):
    return subprocess.check_output(['kubectl','-n','otel-demo',*args,'-o','json'],
                                   text=True,timeout=30)


def sha(data):
    return hashlib.sha256(data.encode()).hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    objects={}
    for kind,name in OBJECTS:
        raw=kube('get',kind,name)
        parsed=json.loads(raw)
        path=args.output/(kind+'-'+name+'.json')
        path.write_text(json.dumps(parsed,indent=2)+'\n')
        objects[kind+'/'+name]={'path':path.name,'uid':parsed['metadata']['uid'],
                                 'resource_version':parsed['metadata']['resourceVersion'],
                                 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    pods=json.loads(kube('get','pods'))
    relevant=[item for item in pods['items'] if item['metadata']['labels'].get(
        'opentelemetry.io/name') in SERVICES]
    pod_path=args.output/'pods-before.json'
    pod_path.write_text(json.dumps(relevant,indent=2)+'\n')
    result={'at_utc':datetime.now(timezone.utc).isoformat(),'objects':objects,
            'pods_sha256':hashlib.sha256(pod_path.read_bytes()).hexdigest(),
            'frozen_stage5_v2_untouched':True}
    (args.output/'backup-manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':'LIVE_BACKUP_COMPLETE','objects':len(objects),
                      'target_pods':len(relevant)}))


if __name__=='__main__': main()
