#!/usr/bin/env python3
"""Formal ordinary business streams with a lease-bound home delay window."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import threading
import time
import urllib.error
import urllib.request
import uuid

import yaml


PATHS={'image_asset':'/images/products/OpticalTubeAssembly.jpg',
       'cart_view':'/api/cart',
       'home':'/',
       'recommendations':'/api/recommendations?productIds=0PUK6V6EV0'}


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()


def fault_delay(contract, operation, at):
    if operation != 'home':
        return None
    start = datetime.fromisoformat(contract['start_utc'].replace('Z', '+00:00')).timestamp()
    end = datetime.fromisoformat(contract['end_utc'].replace('Z', '+00:00')).timestamp()
    if not start <= at < end:
        return None
    lease_path = Path(contract['lease_path'])
    if lease_path.is_symlink():
        raise RuntimeError('HTTP fault lease is a symlink')
    lease = json.loads(lease_path.read_text())
    issued = datetime.fromisoformat(lease['issued_utc'].replace('Z', '+00:00')).timestamp()
    expires = datetime.fromisoformat(lease['expires_utc'].replace('Z', '+00:00')).timestamp()
    if (lease.get('run_id') != contract['run_id'] or
            lease.get('plan_sha256') != contract['plan_sha256'] or
            lease.get('permission') != 'BOUND_HTTP_REQDELAY' or
            lease.get('safety_pass') is not True or
            not 0 < expires - issued <= 4.01 or
            not issued - 0.5 <= at <= expires):
        raise RuntimeError('HTTP fault lease is invalid or expired')
    return contract['delay_ms']


def request(base,operation,index,seed,timeout,contract,output,lock):
    rng=random.Random((seed<<32)+index)
    trace_id=f'{rng.getrandbits(128):032x}'
    span_id=f'{rng.getrandbits(64):016x}'
    if trace_id=='0'*32 or span_id=='0'*16: raise ValueError('Invalid trace context')
    user=str(uuid.uuid5(uuid.NAMESPACE_URL,f'oc-control:{seed}:{operation}:{index}'))
    headers={'User-Agent':'business-control/1.0',
             'baggage':'synthetic_request=true,session.id='+user,
             'traceparent':f'00-{trace_id}-{span_id}-01'}
    at=time.time()
    delay=fault_delay(contract,operation,at)
    if delay is not None:
        headers['x-envoy-fault-delay-request']=str(delay)
    if operation=='home':
        event={'at_utc':datetime.fromtimestamp(at,timezone.utc).isoformat(),
               'trace_id':trace_id,'delay_header_applied':delay is not None,
               'requested_delay_ms':delay}
        with lock:
            with (output/'home-header-events.jsonl').open('a') as stream:
                stream.write(json.dumps(event)+'\n')
    began=now()
    with lock:
        with (output/'request-dispatch-journal.jsonl').open('a') as stream:
            stream.write(json.dumps({'trace_id':trace_id,'operation':operation,
                                     'at_utc':began,'state':'HTTP_DISPATCH_ATTEMPT'})+'\n')
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
    row={'index':index,'operation':operation,'path':PATHS[operation],
            'time_utc':now(),'trace_id':trace_id,'status_code':status,
            'error_type':error,'latency_ms':round((time.monotonic()-started)*1000,3),
            'success':status is not None and 200<=status<400,
            'request_started_utc':began}
    with lock:
        with (output/'control-events-live.jsonl').open('a') as stream:
            stream.write(json.dumps(row)+'\n')
        if operation in ('image_asset','cart_view') and not row['success']:
            marker=output/(operation+'-failure.json')
            if not marker.exists():
                marker.write_text(json.dumps(row)+'\n')
    return row


def run_stream(base,spec,seed,duration,timeout,concurrency,output,contract,lock):
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
                active.add(pool.submit(request,base,name,index,seed,timeout,contract,output,lock))
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
    parser.add_argument('--registration',type=Path,required=True)
    parser.add_argument('--registration-sha256',required=True)
    args=parser.parse_args()
    plan=yaml.safe_load(args.plan.read_text())
    workload=plan['workload']
    streams=workload['control_streams']
    if args.registration.is_symlink() or sha(args.registration)!=args.registration_sha256:
        raise RuntimeError('Formal fault registration hash mismatch')
    registration=json.loads(args.registration.read_text())
    fault=registration['request_delay']
    start=datetime.fromisoformat(registration['start_utc'].replace('Z','+00:00'))
    fault_start=start.timestamp()+fault['offset_seconds']
    contract={**fault,'run_id':registration['id'],
              'plan_sha256':args.registration_sha256,
              'start_utc':datetime.fromtimestamp(fault_start,timezone.utc).isoformat(),
              'end_utc':datetime.fromtimestamp(
                  fault_start+fault['duration_seconds'],timezone.utc).isoformat()}
    if (registration['id']!=plan['run_id'] or
            plan['fault_plan_sha256']!=args.registration_sha256 or
            registration['start_utc']!=args.start_utc or
            fault['operation']!='home' or fault['delay_ms']!=250 or
            fault['duration_seconds']!=60 or args.duration!=1800):
        raise RuntimeError('Formal HTTP delay registration is not bound to this run')
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
        'normal_business_control':True,'fault_injected_by_generator':False,
        'registration_sha256':args.registration_sha256},indent=2)+'\n')
    lock=threading.Lock()
    with ThreadPoolExecutor(max_workers=len(streams)) as pool:
        futures={pool.submit(run_stream,args.base_url,item,args.seed+item['seed_offset'],
            args.duration,workload['control_timeout_seconds'],
            workload['control_concurrency_per_stream'][item['name']],args.output,
            contract,lock):item['name']
            for item in streams}
        results={name:future.result() for future,name in futures.items()}
    status='CONTROL_STREAMS_COMPLETE' if all(row['planned_requests']==row['recorded_requests']
        for row in results.values()) else 'CONTROL_STREAMS_INCOMPLETE'
    events=[json.loads(line) for line in (args.output/'home-header-events.jsonl').read_text().splitlines()]
    applied=sum(row['delay_header_applied'] for row in events)
    if applied<plan['minimum_fixed_stream_delayed_home_requests']:
        status='HTTP_FAULT_TARGET_COVERAGE_INSUFFICIENT'
    summary={'status':status,'started_utc':began,'ended_utc':now(),'streams':results,
             'plan_sha256':sha(args.plan),'fault_injected_by_generator':False,
             'delayed_home_requests':applied,'home_header_events_sha256':sha(args.output/'home-header-events.jsonl')}
    (args.output/'result-manifest.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({'status':status,'streams':{name:{'planned':row['planned_requests'],
        'successes':row['successful_requests'],'missed':row['missed_arrivals']}
        for name,row in results.items()}}))
    if status!='CONTROL_STREAMS_COMPLETE':raise SystemExit(1)


if __name__=='__main__':main()
