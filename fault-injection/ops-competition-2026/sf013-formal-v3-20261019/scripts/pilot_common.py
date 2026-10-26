import sys,json,hashlib,subprocess,time,urllib.request,urllib.parse
from datetime import datetime,timezone
from pathlib import Path
OPS=Path('/root/ops-competition-2026')
PROD=OPS/'sf002-production-20261014'
DATA=Path('/data/stage5-v3/ops-competition-2026')
import os
RUN=Path(os.environ['SF013_RUN']).resolve()
ALLOWED_RUNS={DATA/'runs/SF-013-ATTEMPT-03'}
if RUN not in ALLOWED_RUNS:
    raise RuntimeError('Only an exact registered SF-013 dry-run or attempt path is allowed')
REGISTRY=PLAN=None
RUN_ID=RUN.name
CONTROL=RUN/'control'
REPAIR=OPS/'collector-telemetry-repair-20261013'
TELEMETRY=REPAIR/'results/production-telemetry-readiness-v2.json'
EXPECTED_CONFIG='ad42e84b2a1d25b6b9b716f2a119f8d3ab6c50a975b860f3a8eed939618dd05f'
KUBE=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','-n','otel-demo']
PROM='http://10.97.14.138:9090';OS='http://10.111.114.255:9200'
OFFSETS=list(range(0,1801,300))
sys.path.insert(0,str(PROD/'scripts'))
sys.path.append(str(OPS/'scripts'))
def now():return datetime.now(timezone.utc).isoformat()
def stamp(s):return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(4*1024**2),b''):h.update(b)
    return h.hexdigest()
def read(p):return json.loads(Path(p).read_text())
def save(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
    p.chmod(0o600)
def http(url,body=None):
    req=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=20) as f:return json.load(f)
def query(q):return http(PROM+'/api/v1/query?'+urllib.parse.urlencode({'query':q}))['data']['result']
def kube(kind,name=''):return json.loads(subprocess.check_output(KUBE+['get',kind,*([name] if name else []),'-o','json'],text=True,timeout=25))
def collector():
    rows=[p for p in kube('pods')['items'] if p['metadata']['name'].startswith('otel-collector-') and not p['metadata'].get('deletionTimestamp')]
    if len(rows)!=1:raise RuntimeError('Collector identity not unique')
    return rows[0]
def telemetry_state(expected_uid=None):
    p=collector();name=p['metadata']['name']
    if expected_uid and p['metadata']['uid']!=expected_uid:raise RuntimeError('Collector UID changed')
    if not all(c.get('ready') and c.get('restartCount')==0 for c in p['status']['containerStatuses']):raise RuntimeError('Collector restart/readiness failure')
    relay=kube('configmap','otel-collector')['data']['relay'];digest=hashlib.sha256(relay.encode()).hexdigest()
    if digest!=EXPECTED_CONFIG:raise RuntimeError('Verified Collector configuration changed')
    metrics=query('{k8s_pod_name="'+name+'",__name__=~"otelcol_(receiver_.*|exporter_.*|process_.*|processor_.*)"}')
    refusal={}
    for transport in ('grpc','http'):
        rows=[r for r in metrics if r['metric'].get('__name__')=='otelcol_receiver_refused_spans_total' and r['metric'].get('transport')==transport]
        if len(rows)!=1:raise RuntimeError('Required receiver-refused series missing or ambiguous')
        refusal[transport]=float(rows[0]['value'][1])
    memory=query('container_memory_working_set_bytes{namespace="otel-demo",pod="'+name+'",container="opentelemetry-collector"}')
    if len(memory)!=1 or float(memory[0]['value'][1])>=380*1024**2:raise RuntimeError('Collector working-set safety bound')
    freshness={}
    for signal,index,field in [('logs','otel-logs-'+datetime.now(timezone.utc).date().isoformat(),'@timestamp'),
        ('traces','jaeger-jaeger-span-'+datetime.now(timezone.utc).date().isoformat(),'startTime')]:
        result=http(OS+'/'+index+'/_search',{'size':1,'query':{'match_all':{}},'sort':[{field:'desc'}]})
        if result['_shards']['total']<1 or result['_shards']['failed'] or not result['hits']['hits']:raise RuntimeError('Storage query has no valid source')
        v=result['hits']['hits'][0]['_source'][field];s=stamp(v) if signal=='logs' else v/1e6
        freshness[signal]={'value':v,'age_seconds':time.time()-s}
        if not 0<=freshness[signal]['age_seconds']<=60:raise RuntimeError('Telemetry storage freshness failed')
    return {'at_utc':now(),'uid':p['metadata']['uid'],'pod':name,'config_sha256':digest,
        'refused':refusal,'working_set_bytes':float(memory[0]['value'][1]),'metrics':metrics,'freshness':freshness,
        'missing_optional_counters':'UNKNOWN_NOT_ZERO'}
def live_requests(run=RUN):
    rows=[]
    for relative in ['state-machine/request-events-live.jsonl','controls/control-events-live.jsonl']:
        p=Path(run)/relative
        if not p.exists():continue
        # Ignore only an unfinished final write while a producer is active; never repair a completed file.
        data=p.read_bytes();lines=data.splitlines()
        if data and not data.endswith(b'\n'):lines=lines[:-1]
        for line in lines:
            r=json.loads(line);r['stream']='business' if relative.startswith('state-machine') else r['operation'];rows.append(r)
    return rows
def boundary_match(request,span,window_start,window_end):
    if request['trace_id'].lower()!=span['traceID'].lower() or span['process']['serviceName']!='frontend-proxy' or span.get('tag',{}).get('span@kind')!='server':return False
    client=stamp(request['request_started_utc']);server=span['startTime']/1e6
    return (window_start<=client<window_start+1 and window_start-1<=server<window_start) or (window_end-1<=client<window_end and window_end<=server<window_end+1)
