#!/usr/bin/env python3
"""Independent fixed-rate ordinary business branches for path controls."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import time
import urllib.error
import urllib.request
import uuid

import yaml


PATHS={'image_asset':'/images/products/OpticalTubeAssembly.jpg',
       'cart_view':'/api/cart'}


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()


def request(base,operation,index,seed,timeout):
    rng=random.Random((seed<<32)+index)
    trace_id=f'{rng.getrandbits(128):032x}'
    span_id=f'{rng.getrandbits(64):016x}'
    if trace_id=='0'*32 or span_id=='0'*16: raise ValueError('Invalid trace context')
    user=str(uuid.uuid5(uuid.NAMESPACE_URL,f'oc-control:{seed}:{operation}:{index}'))
    headers={'User-Agent':'oc-business-workflow/1.0',
             'baggage':'synthetic_request=true,session.id='+user,
             'traceparent':f'00-{trace_id}-{span_id}-01'}
    started=time.monotonic()
    status,error=None,None
    try:
        with urllib.request.urlopen(urllib.request.Request(base.rstrip('/')+PATHS[operation],
                headers=headers,method='GET'),timeout=timeout) as response:
            status=response.status
            response.read()
    except urllib.error.HTTPError as exc:
        status,error=exc.code,'HTTPError'
    except Exception as exc:
        error=type(exc).__name__
    return {'index':index,'operation':operation,'path':PATHS[operation],
            'time_utc':now(),'trace_id':trace_id,'status_code':status,
            'error_type':error,'latency_ms':round((time.monotonic()-started)*1000,3),
            'success':status is not None and 200<=status<400}


def run_stream(base,spec,seed,duration,timeout,concurrency,output):
    name=spec['name']
    rate=float(spec['rate_per_second'])
    if name not in PATHS or rate<=0 or concurrency<1:
        raise ValueError('Unverified business control route or invalid rate')
    start=time.monotonic()
    end=start+duration
    rows=[];active=set();index=0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        while True:
            target=start+index/rate
            if target>=end: break
            time.sleep(max(0,target-time.monotonic()))
            completed={future for future in active if future.done()}
            active.difference_update(completed)
            rows.extend(future.result() for future in completed)
            if time.monotonic()-target>1.0 or len(active)>=concurrency:
                rows.append({'index':index,'operation':name,'scheduled_utc':
                    datetime.fromtimestamp(time.time()-(time.monotonic()-target),timezone.utc).isoformat(),
                    'status':'MISSED_ARRIVAL','success':False,'trace_id':None,
                    'latency_ms':None,'error_type':'SCHEDULE_LATE_OR_STREAM_FULL'})
            else:
                active.add(pool.submit(request,base,name,index,seed,timeout))
            index+=1
        rows.extend(future.result() for future in active)
    rows.sort(key=lambda row:row['index'])
    file=output/(name+'.jsonl')
    with file.open('x') as stream:
        for row in rows:
            stream.write(json.dumps(row,separators=(',',':'))+'\n')
    valid=[row for row in rows if row.get('trace_id')]
    return {'operation':name,'rate_per_second':rate,'planned_requests':index,
            'recorded_requests':len(rows),'executed_requests':len(valid),
            'successful_requests':sum(row['success'] for row in valid),
            'missed_arrivals':len(rows)-len(valid),
            'requests_sha256':sha(file)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--seed',type=int,required=True)
    parser.add_argument('--start-utc',required=True)
    parser.add_argument('--duration',type=int,required=True)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    plan=yaml.safe_load(args.plan.read_text())
    workload=plan['workload']
    streams=workload['control_streams']
    if ({row['name'] for row in streams}!=set(PATHS) or
            args.duration!=workload['duration_seconds'] or args.output.exists()):
        raise ValueError('Control streams or duration differ from predeclared plan')
    args.output.mkdir(parents=True)
    scheduled=datetime.fromisoformat(args.start_utc.replace('Z','+00:00')).timestamp()
    wait=scheduled-time.time()
    if not 1<=wait<=180:raise ValueError('Scheduled start is not fresh')
    time.sleep(wait)
    began=now()
    (args.output/'input-manifest.json').write_text(json.dumps({
        'plan_sha256':sha(args.plan),'generator_sha256':sha(Path(__file__)),
        'seed':args.seed,'scheduled_start_utc':args.start_utc,'actual_start_utc':began,
        'duration_seconds':args.duration,'base_url':args.base_url,
        'normal_business_control':True,'fault_injected_by_generator':False},indent=2)+'\n')
    with ThreadPoolExecutor(max_workers=len(streams)) as pool:
        futures={pool.submit(run_stream,args.base_url,item,args.seed+item['seed_offset'],
            args.duration,workload['control_timeout_seconds'],
            workload['control_concurrency_per_stream'][item['name']],args.output):item['name']
            for item in streams}
        results={name:future.result() for future,name in futures.items()}
    status='CONTROL_STREAMS_COMPLETE' if all(row['planned_requests']==row['recorded_requests']
        for row in results.values()) else 'CONTROL_STREAMS_INCOMPLETE'
    summary={'status':status,'started_utc':began,'ended_utc':now(),'streams':results,
             'plan_sha256':sha(args.plan),'fault_injected_by_generator':False}
    (args.output/'result-manifest.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({'status':status,'streams':{name:{'planned':row['planned_requests'],
        'successes':row['successful_requests'],'missed':row['missed_arrivals']}
        for name,row in results.items()}}))
    if status!='CONTROL_STREAMS_COMPLETE':raise SystemExit(1)


if __name__=='__main__':main()
