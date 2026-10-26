#!/usr/bin/env python3
"""Archive completed before/during/after telemetry using frozen Stage4F reader."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess


CAPTURE=Path('/root/stage4f-cross-layer-validation/scripts/capture_live.py')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--attempt',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Refusing to overwrite completed capture')
    schedule=json.loads((args.attempt/'schedule.json').read_text())
    start=datetime.fromisoformat(schedule['scheduled_start_utc'])
    windows={'before':(start,start+timedelta(seconds=120)),
             'during':(start+timedelta(seconds=120),start+timedelta(seconds=180)),
             'after':(start+timedelta(seconds=180),start+timedelta(seconds=300))}
    if datetime.now(timezone.utc)<windows['after'][1]:
        raise ValueError('Capture window has not finished')
    args.output.mkdir(parents=True)
    results={}
    for phase,(begin,end) in windows.items():
        path=args.output/phase
        command=['python3',str(CAPTURE),'--phase',phase,
                 '--start-utc',begin.isoformat(),'--end-utc',end.isoformat(),
                 '--trace-source','JAEGER_OPENSEARCH_STORAGE',
                 '--output',str(path)]
        result=subprocess.run(command,capture_output=True,text=True,timeout=180)
        results[phase]={'command':command,'returncode':result.returncode,
            'stdout':result.stdout[-1000:],'stderr':result.stderr[-1000:],
            'window':[begin.isoformat(),end.isoformat()]}
        if result.returncode:
            (args.output/'capture-status.json').write_text(json.dumps({
                'status':'CAPTURE_INCOMPLETE','source_sha256':sha(CAPTURE),
                'phases':results},indent=2)+'\n')
            raise RuntimeError(phase+' capture failed')
        manifest=path/'capture-manifest.json'
        if not manifest.exists():
            manifest=path/'manifest.json'
        if manifest.exists():results[phase]['manifest_sha256']=sha(manifest)
    data={'status':'THREE_PHASE_CAPTURE_COMPLETE','source_sha256':sha(CAPTURE),
          'trace_source':'JAEGER_OPENSEARCH_STORAGE','phases':results,
          'gt_read':False,'stage5_v2_source_unmodified':True}
    (args.output/'capture-status.json').write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps({'status':data['status'],'phases':list(results)}))


if __name__=='__main__':main()
