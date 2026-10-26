import json,time
from pathlib import Path
from datetime import datetime,timezone
c=Path('/data/stage5-v3/ops-competition-2026/runtime/SF-PILOT-01-ATTEMPT-04');r=Path('/data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01-ATTEMPT-04')
def read(p):return json.loads(p.read_text())
v={'utc':datetime.now(timezone.utc).isoformat()}
if (c/'heartbeat.json').exists():
 h=read(c/'heartbeat.json');co=h['checks']['collector'];v.update(heartbeat_sequence=h['sequence'],heartbeat_utc=h['completed_utc'],collector_mib=round(co['working_set_bytes']/1024**2,2),refused=co['refused'])
 v['exporter_failed']=[{'name':x['metric']['__name__'],'exporter':x['metric'].get('exporter'),'value':x['value'][1]} for x in co['metrics'] if x['metric']['__name__'].startswith('otelcol_exporter_') and 'failed' in x['metric']['__name__']]
if (c/'workflow.jsonl').exists():v['last_events']=[{k:x[k] for k in ('at_utc','phase','tag','action','error') if k in x} for x in [json.loads(l) for l in (c/'workflow.jsonl').read_text().splitlines()[-3:]]]
for name,p in [('controller',c/'controller-terminal.json'),('observer',r/'observation-result.json')]:
 if p.exists():
  x=read(p);v[name]={k:x[k] for k in ('status','error','cleanup_errors','post_health_error','completed_utc') if k in x}
for stream,name in [('state-machine','request-events-live.jsonl'),('controls','control-events-live.jsonl')]:
 p=r/stream/name
 if p.exists():
  rows=[json.loads(l) for l in p.read_text().splitlines() if l];v[stream]={'completed':len(rows),'failed':sum(not x['success'] for x in rows)}
v['chunks']={p.parent.name:read(p)['status'] for p in (r/'capture').glob('*/quality.json')}
v['byte_reservations']=int((c/'json-byte-reservations').read_text()) if (c/'json-byte-reservations').exists() else 0
print(json.dumps(v))
