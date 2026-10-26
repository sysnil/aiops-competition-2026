import json,hashlib,subprocess,urllib.request,urllib.parse
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path('/root/ops-competition-2026/low-stable-v4-20261017')
DATA=Path('/data/stage5-v3/ops-competition-2026/low-stable-v4-20261017')
KUBE=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','-n','otel-demo']
PROM='http://10.97.14.138:9090';OS='http://10.111.114.255:9200'
def now():return datetime.now(timezone.utc).isoformat()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def save(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
    p.chmod(0o600)
def kube(*args,timeout=45):return subprocess.check_output(KUBE+list(args),text=True,timeout=timeout)
def obj(kind,name=''):return json.loads(kube('get',kind,*([name] if name else []),'-o','json'))
def http(url,body=None):
    req=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=35) as f:return json.load(f)
def prom(q):return http(PROM+'/api/v1/query?'+urllib.parse.urlencode({'query':q}))
def pod(prefix):
    rows=[r for r in obj('pods','')['items'] if r['metadata']['name'].startswith(prefix) and not r['metadata'].get('deletionTimestamp')]
    if len(rows)!=1:raise RuntimeError('Nonunique '+prefix)
    return rows[0]
def admin(path):
    proxy=pod('frontend-proxy-');address=proxy['status']['podIP']
    with urllib.request.urlopen('http://'+address+':10000/'+path,timeout=10) as f:return f.read().decode()
