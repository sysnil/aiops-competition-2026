"""Read-only, bounded code/configuration export; never exports credentials or telemetry."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
from datetime import datetime, timezone

HOST = sys.argv[1]
SKIP = {'.git','.venv','.python','.pytest_cache','__pycache__','node_modules','backups','backup',
        'runs','run','runtime','private-runtime','snapshots','observations','figures','logs','cache',
        'exports','export-work','packages','raw','data','indexes','results','attempts','attempt01',
        'final-case-auth-secrets-20261019','credentials','secrets','venv','build','dist',
        'export-runtime','site-packages','cp312','cp311','python-runtime'}
TEXT = {'.py','.sh','.md','.json','.yaml','.yml','.toml','.txt','.ini','.cfg','.service','.timer',
        '.conf','.html','.js','.css','.svg','.lock','.example'}
SECRET_PATTERNS = [
    ('llm_api_key',re.compile(r'(?<![A-Za-z0-9])(?:sk-|ark-)[A-Za-z0-9_-]{20,}')),
    ('github_token',re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})')),
    ('team_token',re.compile(r'team\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{24,}')),
    ('bearer',re.compile(r'(?i)(Bearer\s+)[A-Za-z0-9_.-]{28,}')),
    ('host_password',re.compile(r'(?i)\b(?:open|pjs)@\d{4}\b')),
    ('private_key',re.compile(r'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----[\s\S]*?-----END (?:OPENSSH |RSA |EC )?PRIVATE KEY-----')),
]
ASSIGN = re.compile(r'''(?im)(["']?(?:api_key|apikey|access_token|auth_token|team_token|review_token|run_token|password|secret_key|shared_secret|client_secret)["']?\s*[:=]\s*["'])([^"'\r\n]{8,})(["'])''')

def clean(raw):
    text=raw.decode('utf-8');changes=[]
    for label,pattern in SECRET_PATTERNS:
        text,n=pattern.subn('[REDACTED_CONFIGURE_LOCALLY]',text)
        if n:changes.append({'kind':label,'count':n})
    def redact(m):
        value=m[2]
        if (re.fullmatch(r'[A-Z][A-Z0-9_]+',value) or any(x in value.lower() for x in
            ('example','placeholder','replace','your_','your-','redacted','getenv','environ','${','test','dummy','synthetic'))):
            return m[0]
        if any(c.isspace() for c in value) or len(set(value))<7:return m[0]
        changes.append({'kind':'credential_assignment','count':1})
        return m[1]+'[REDACTED_CONFIGURE_LOCALLY]'+m[3]
    text=ASSIGN.sub(redact,text)
    return text.encode(),changes

def allowed_file(p,rel,mode):
    name=p.name.lower()
    if p.is_symlink():return False
    if name in {'known_hosts','authorized_keys','id_rsa','id_ed25519','team.env','agent-llm.env'}:return False
    if p.suffix.lower() in {'.db','.sqlite','.sqlite3','.parquet','.xlsx','.xls','.csv','.pem','.key','.tar','.gz','.tgz','.zip','.log','.jsonl','.pyc','.p12'}:return False
    if name.endswith('.env') or name.startswith(('.env','grader.env','reviewer.env')):return False
    if any(x in name for x in ('.before','.bak','_ori','-before-','credential-list','password-list')):return False
    if mode=='qa' and p.suffix.lower()=='.png':return True
    if p.suffix.lower() not in TEXT:return False
    parts=rel.parts
    if mode=='injection':
        if 'semifinal-qa-benchmark-v1' in parts or 'diagnostics' in parts:return False
        if p.suffix in {'.json','.yaml','.yml'} and not any(x in parts for x in ('plans','design','schema','schemas','contracts','config','catalog')):return False
    if mode=='support' and parts[0] not in {'scripts','design','tests','catalog','schemas','schema','config'} and len(parts)>1:return False
    if mode=='server' and parts[0] not in {'app','client','config','grading','scripts','tests','examples','semifinal-operations'} and len(parts)>1:return False
    if mode=='organizer' and parts[0] not in {'tests','tools'} and len(parts)>1:return False
    if mode=='team-code' and p.suffix not in {'.py','.md'}:return False
    if mode=='participant' and parts[0] not in {'agent','client','detectors','schemas','mcp_servers','skills','scripts','tests','webapp','configs','examples','tools'} and len(parts)>1:return False
    if mode=='qa' and parts[0] not in {'public','private','scripts','schemas','validation'} and len(parts)>1:return False
    return True

specs=[]
if HOST=='server51':
    specs=[('/opt/aiops-contest-server','server','server'),
           ('/opt/aiops-competition-visualization','organizer/visualization','organizer'),
           ('/opt/aiops-competition-submissions','organizer/submissions','organizer'),
           ('/opt/aiops-contest-server/team-provisioning','organizer/team-management','team-code'),
           ('/etc/aiops-private-grader/final-gt-v2-20261020','private/ground-truth/final','all'),
           ('/etc/aiops-private-grader/qa-gt','private/ground-truth/semifinal','all'),
           ('/etc/aiops-private-grader/qa-gt-preview','private/ground-truth/practice','all')]
    singles=[('/etc/aiops-private-grader/formal-final-rubric-v2-newcases.json','private/scoring/formal-final-rubric.json'),
             ('/etc/aiops-contest/final-event-20261023.json','questions/final/event-plan.json'),
             ('/etc/aiops-submission-review/final-qualifier-scoring-plan.json','organizer/scoring-plans/final-qualifier.json')]
    singles.append(('/etc/aiops-contest/semifinal-scoring-plan.json','organizer/scoring-plans/semifinal.json'))
    for unit in ('aiops-contest-api.service','aiops-private-grader-demo.service','aiops-competition-visualization.service',
                 'aiops-competition-submissions.service','aiops-submission-snapshot.service','aiops-submission-snapshot.timer'):
        singles.append(('/etc/systemd/system/'+unit,'deploy/systemd/'+unit))
elif HOST=='master':
    specs=[('/root/ops-competition-2026','fault-injection/ops-competition-2026','injection'),
           ('/root/ops-competition-2026/semifinal-qa-benchmark-v1','questions/semifinal/generation','qa')]
    projects=['cross-layer-observability-control-rehearsal','stage5-v3-observability-diagnostics',
              'cross-layer-workload-generation','stage5-v3-autonomous-preparation','stage5-v3-clean-gate-v2',
              'stage5-v3-final-fast-ready','stage5-v3-fast-ready','stage5-v3-mechanism-qualification',
              'cross-layer-rehearsal-phase4','cross-layer-fault-rehearsal','stage5-v3-collector-reliability',
              'stage4f-cross-layer-validation']
    specs += [('/root/'+name,'fault-injection/support/'+name,'support') for name in projects]
    singles=[]
elif HOST=='organizer50':
    specs=[('/opt/aiops-competition/participant-starter','participant/demo','participant')]
    singles=[('/etc/systemd/system/aiops-agent-web.service','deploy/systemd/aiops-agent-web.service')]
else:raise SystemExit('Unknown export host')

files={};entries=[];missing=[];skipped_large=[]
def add(p,destination,mode):
    if destination in files:raise RuntimeError('Duplicate destination')
    if p.stat().st_size>8_000_000:skipped_large.append(destination);return
    before=p.stat();raw=p.read_bytes();after=p.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise RuntimeError('Source changed during read: '+str(p))
    if p.suffix.lower()=='.png':data=raw;changes=[]
    else:
        try:data,changes=clean(raw)
        except UnicodeDecodeError:raise RuntimeError('Unexpected binary source: '+str(p))
    files[destination]=data
    entries.append({'path':destination,'source_host':HOST,'source_path':str(p),'source_sha256':hashlib.sha256(raw).hexdigest(),
                    'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'sanitization':changes})

for source,prefix,mode in specs:
    root=Path(source)
    if not root.exists():missing.append(source);continue
    for parent,dirs,names in os.walk(root,followlinks=False):
        dirs[:]=sorted(d for d in dirs if d not in SKIP and not d.startswith('.') and not (Path(parent)/d).is_symlink())
        for name in sorted(names):
            p=Path(parent)/name;rel=p.relative_to(root)
            if allowed_file(p,rel,mode):add(p,prefix+'/'+rel.as_posix(),mode)
for source,destination in singles:
    p=Path(source)
    if p.is_file():add(p,destination,'all')
    else:missing.append(source)

# Catalog-only data references; never read Metrics/Logs/Traces Parquet.
if HOST=='server51':
    root=Path('/data/aiops-contest/public')
    if (root/'manifests').exists():
        for manifest in sorted((root/'manifests').glob('SF-*.json')):
            data=json.loads(manifest.read_text());case=data['case_id']
            if not re.fullmatch(r'SF-\d{3}',case):continue
            add(manifest,'datasets/public-catalog/'+case+'/manifest.json','all')
            directory=root/'cases'/case/str(data['version'])
            for name in ('metadata.json','topology.json'):
                p=directory/name
                if p.is_file():add(p,'datasets/public-catalog/'+case+'/'+name,'all')

metadata={'host':HOST,'snapshot_utc':datetime.now(timezone.utc).isoformat(),'files':entries,'missing_sources':missing,
          'excluded_large_files':skipped_large,'scope':'code, question definitions, private grading assets and small manifests; no credentials or raw telemetry'}
files['provenance/'+HOST+'.json']=(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n').encode()
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
    for name,data in sorted(files.items()):
        info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644;info.mtime=int(datetime.now(timezone.utc).timestamp())
        archive.addfile(info,io.BytesIO(data))
sys.stderr.write(json.dumps({'host':HOST,'files':len(entries),'bytes':sum(x['bytes'] for x in entries),
                            'sanitized_files':sum(bool(x['sanitization']) for x in entries),'missing_sources':missing})+'\n')
