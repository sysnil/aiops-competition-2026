#!/usr/bin/env python3
"""Read-only SHA inventory and restored-read verification for competition archives."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()

def inventory(root):
    rows=[]
    for p in sorted(root.rglob('*')):
        if p.is_symlink():raise RuntimeError('Archive source contains symlink: '+str(p))
        if p.is_file():rows.append({'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':sha(p)})
    return rows

def restored_reads(root,rows):
    samples=[]
    for modality,marker in [('metrics','/metrics/'),('logs','/logs/'),('traces','/trace-storage-pages/')]:
        choices=[r['path'] for r in rows if marker in '/'+r['path'] and r['path'].endswith('.json')]
        if not choices:continue
        rel=sorted(choices)[0];v=json.loads((root/rel).read_text())
        if modality=='metrics':
            if v.get('status')!='success':raise RuntimeError('Archived metric sample failed')
            count=len(v['data']['result'])
        else:count=len(v['hits']['hits'])
        samples.append({'modality':modality,'file':rel,'records_or_series':count})
    return samples

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['create','verify'])
    p.add_argument('--root',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path);a=p.parse_args()
    root=a.root.resolve()
    allowed=('/data/stage5-v3/ops-competition-2026','/root/ops-competition-2026','/data/rca-archives/ops-competition-2026/')
    if not any(str(root)==r or str(root).startswith(r.rstrip('/')+'/') for r in allowed):raise RuntimeError('Unregistered archive root')
    if a.manifest.resolve().is_relative_to(root):raise RuntimeError('Manifest must remain outside inventoried tree')
    rows=inventory(root);reads=restored_reads(root,rows)
    if a.action=='create':
        output=a.manifest;value={'status':'COMPETITION_ARCHIVE_SOURCE_MANIFEST','source':str(root),
            'files':rows,'file_count':len(rows),'bytes':sum(r['bytes'] for r in rows),'restored_reads':reads}
    else:
        expected=json.loads(a.manifest.read_text())
        if rows!=expected['files'] or reads!=expected['restored_reads']:raise RuntimeError('Archive SHA/restore mismatch')
        output=a.output;value={'status':'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS','root':str(root),
            'source_manifest_sha256':sha(a.manifest),'file_count':len(rows),'bytes':sum(r['bytes'] for r in rows),
            'restored_reads':reads,'host':socket.gethostname()}
    value['at_utc']=datetime.now(timezone.utc).isoformat()
    output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with output.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    output.chmod(0o600)
    print(json.dumps({k:v for k,v in value.items() if k!='files'}))

if __name__=='__main__':main()
