import json,datetime,subprocess
from pathlib import Path
run='SF-PILOT-01-ATTEMPT-03';d=Path('/data/stage5-v3/ops-competition-2026');c=d/'attempt03-control'/run;r=d/'runs'/run
v={'now':datetime.datetime.now(datetime.timezone.utc).isoformat()}
if (c/'heartbeat.json').exists():
 h=json.loads((c/'heartbeat.json').read_text());v.update(seq=h['sequence'],heartbeat=h['completed_utc'],refused=h['checks']['collector']['refused'],collector_MiB=round(h['checks']['collector']['working_set_bytes']/1048576,2),root_free=h['checks']['environment'].get('disk',{}).get('root_free_bytes'))
if (r/'schedule.json').exists():
 s=json.loads((r/'schedule.json').read_text());v['elapsed_minutes']=round((datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(s['start_utc'])).total_seconds()/60,2)
for name in ('ready-receipt.json','controller-terminal.json'):
 if (c/name).exists():
  t=json.loads((c/name).read_text());v[name]={k:t[k] for k in t if k in ('status','error','startup_seconds','first_check_seconds','completed_utc','cancelled','post_health_error')}
v['onsets']=[{k:v for k,v in json.loads(p.read_text()).items() if k in ('actual_worker_utc','worst_case_deviation_seconds','feedback_upper_seconds','pass')} for p in c.glob('*immediate-onset.json')]
v['chunks']={p.parent.name:json.loads(p.read_text())['status'] for p in r.glob('capture/*/quality.json')}
if (r/'observation-result.json').exists():v['observation']=json.loads((r/'observation-result.json').read_text())
print(json.dumps(v))
