"""Independent public business/resource recovery check after qualified cleanup actions."""
import argparse,statistics
from pilot_common import *
from competition_observation import safety
def main():
    p=argparse.ArgumentParser();p.add_argument('--layer',choices=['SERVICE','POD'],required=True);a=p.parse_args()
    plan=read(OPS/'plans/SF-PILOT-01.json');now_sec=time.time();rows=live_requests()
    recent=[r for r in rows if now_sec-75<=stamp(r['request_started_utc'])<=now_sec-15]
    if len(recent)<30 or not all(r['success'] for r in recent):raise RuntimeError('Ordinary business recovery not demonstrated')
    s=safety(plan['targets_and_siblings']);t=telemetry_state(plan['collector_uid'])
    if t['refused']!={'grpc':0.0,'http':0.0}:raise RuntimeError('Collector refusal on recovery')
    evidence={'status':'PUBLIC_RECOVERY_PASS','layer':a.layer,'at_utc':now(),'ordinary_requests':len(recent),'safety':s,'telemetry':t}
    if a.layer=='POD':
        fault=read(OPS/'plans/SF-PILOT-01-POD.json');target=fault['targets'][0]
        before=read(RUN/'baseline-prefix-pod/metrics/memory.json')['data']['result']
        selected=[r for r in before if r['metric'].get('pod')==target['name'] and target['uid'].replace('-','_') in r['metric'].get('id','') and r['metric'].get('container') not in ('','POD',None)]
        if len(selected)!=1:raise RuntimeError('Recovery baseline UID series ambiguous')
        values=[float(v) for _,v in selected[0]['values']];median=statistics.median(values);mad=statistics.median(abs(v-median) for v in values)
        metric='container_memory_working_set_bytes{namespace="otel-demo",pod="'+target['name']+'",container!="",container!="POD"}'
        current=query(metric);times=query('timestamp('+metric+')')
        if len(current)!=1 or len(times)!=1 or time.time()-float(times[0]['value'][1])>60:raise RuntimeError('Current recovery memory series unavailable')
        actual=float(current[0]['value'][1]);limit=median+max(16*1024**2,5*mad)
        if actual>limit:raise RuntimeError('Pod working set did not recover')
        evidence['memory']={'baseline_median':median,'current':actual,'allowed_max':limit,'source':'public cAdvisor UID-linked series'}
    save(RUN/('recovery-'+a.layer.lower()+'.json'),evidence)
    print(json.dumps({'status':evidence['status'],'layer':a.layer,'ordinary_requests':len(recent)}))
if __name__=='__main__':main()
