#!/usr/bin/env python3
"""Summarize a completed no-fault run and its archive receipts, read-only."""

import json
import shlex
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
HOSTS = {
    'master': ('192.168.53.52', '/tmp/sfgen-master.sock'),
    'w2': ('192.168.53.54', '/tmp/sfgen-w2.sock'),
    'w3': ('192.168.53.55', '/tmp/sfgen-w3.sock'),
}

def remote(host, code):
    address, socket = HOSTS[host]
    result = subprocess.run(
        ['ssh', '-S', socket, '-o', 'BatchMode=yes', f'root@{address}',
         shlex.join(['python3', '-c', code])],
        capture_output=True, text=True, check=True, timeout=90,
    )
    return json.loads(result.stdout)

def main():
    output = HERE / 'reports/nofault30-summary.json'
    if output.exists():
        raise RuntimeError('Do not overwrite a rehearsal summary')
    master_code = """
import pathlib,json,datetime,shutil
r=pathlib.Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF30-NOFAULT-20261015')
i=pathlib.Path('/data/stage5-v3/ops-competition-archive-index/SF30-NOFAULT-20261015')
s=json.loads((r/'schedule.json').read_text())
t=json.loads((r/'terminal.json').read_text())
a=[json.loads(line) for line in (r/'safety.jsonl').read_text().splitlines()]
q={p.parent.name:json.loads(p.read_text()) for p in (r/'capture').glob('*/quality.json')}
source=json.loads((i/'source-manifest.json').read_text()) if (i/'source-manifest.json').exists() else None
archive=json.loads((i/'result.json').read_text()) if (i/'result.json').exists() else None
start=datetime.datetime.fromisoformat(s['start_utc']).timestamp()
first=q.get('chunk-00')
issued=[];completed=[]
for part,event in [('state-machine','request-events-live.jsonl'),('controls','control-events-live.jsonl')]:
 issued += [json.loads(line)['trace_id'] for line in (r/part/'request-dispatch-journal.jsonl').read_text().splitlines()]
 completed += [json.loads(line)['trace_id'] for line in (r/part/event).read_text().splitlines()]
assert len(issued)==len(completed)==len(set(issued)) and set(issued)==set(completed)
print(json.dumps({'schedule':{'start_utc':s['start_utc'],'end_utc':s['end_utc'],'duration_seconds':s['duration_seconds'],'seed':s['seed'],'faults_injected':s['faults_injected']},
'terminal':t,'safety_samples':len(a),'first_safety_at_utc':a[0]['at_utc'],'last_safety_at_utc':a[-1]['at_utc'],
'request_identity_audit':{'issued':len(issued),'completed':len(completed),'duplicate_or_missing':0},
'collector_working_set_peak_bytes':max(x['collector']['working_set_bytes'] for x in a),
'collector_refused_initial':a[0]['collector']['refused'],'collector_refused_final':a[-1]['collector']['refused'],
'master_root_min_free_bytes':min(x['disk']['root_free_bytes'] for x in a),
'master_data_min_free_bytes':min(x['disk']['data_free_bytes'] for x in a),
'raw_json_reservation_peak_bytes':max(x['disk']['raw_reserved_bytes'] for x in a),
'first_chunk_quality_completed_offset_seconds':datetime.datetime.fromisoformat(first['completed_utc']).timestamp()-start if first else None,
'quality':{k:{'status':v['status'],'first_spans':v.get('first_spans'),'final_spans':v.get('final_spans'),
'audit_spans':v.get('audit_spans'),'logs':v.get('logs'),'completion_lag_seconds':v.get('completion_lag_seconds')}
for k,v in q.items()},'source_archive_manifest':{'bytes':source['bytes'],'files':source['file_count']} if source else None,
'dual_archive':archive,'master_data_free_after':shutil.disk_usage('/data/stage5-v3').free}))
"""
    result = remote('master', master_code)
    worker_code = """
import pathlib,json,shutil
b=pathlib.Path('/data/rca-archives/ops-competition-2026/SF30-NOFAULT-20261015')
v=b/'verification.json';p=b/'preview-v2/manifest.json';c=b/'preview-v2-verification.json'
print(json.dumps({'root_free_bytes':shutil.disk_usage('/').free,'data_free_bytes':shutil.disk_usage('/data').free,
'archive_verification':json.loads(v.read_text()) if v.exists() else None,
'preview_manifest':json.loads(p.read_text()) if p.exists() else None,
'preview_verification':json.loads(c.read_text()) if c.exists() else None}))
"""
    for host in ('w2', 'w3'):
        result[host] = remote(host, worker_code)
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['terminal']['status'],
                      'archive': result['dual_archive']['status'] if result['dual_archive'] else 'PENDING',
                      'preview': result['w2']['preview_verification']['status']
                      if result['w2']['preview_verification'] else 'PENDING'}))

if __name__ == '__main__':
    main()
