#!/usr/bin/env python3
"""Read-only equal-window trace edge counts for no-fault control qualification."""

import argparse
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path


MODULE=Path(__file__).with_name('trace-coverage.py')
spec=importlib.util.spec_from_file_location('trace_coverage',MODULE)
trace=importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing overwrite')
    manifest=json.loads((args.run/'result-manifest.json').read_text())
    start=datetime.fromisoformat(manifest['started_utc'])
    ids=sorted({step['trace_id'] for line in (args.run/'sessions.jsonl').read_text().splitlines()
                for step in json.loads(line)['steps']})
    phases={}
    for index,name in enumerate(('before','during_no_fault','after')):
        begin=start+timedelta(seconds=index*60)
        end=begin+timedelta(seconds=60)
        query={'bool':{'filter':[
            {'range':{'startTimeMillis':{'gte':int(begin.timestamp()*1000),
                                          'lt':int(end.timestamp()*1000)}}},
            {'terms':{'traceID':ids}}]}}
        spans,total=trace.scan(query)
        described=trace.describe(spans)
        phases[name]={'start_utc':begin.isoformat(),'end_utc':end.isoformat(),
                      'opensearch_total':total,'span_count':described['span_count'],
                      'edges':described['edges'],'services':described['services']}
    result={'source':'JAEGER_OPENSEARCH_STORAGE','run':args.run.name,
            'all_three_windows_no_fault':True,'phases':phases}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({name:{key:row['edges'].get(key,0)
        for key in ('frontend-proxy->image-provider','checkout->currency',
                    'frontend->product-catalog')} for name,row in phases.items()}))


if __name__=='__main__':
    main()
