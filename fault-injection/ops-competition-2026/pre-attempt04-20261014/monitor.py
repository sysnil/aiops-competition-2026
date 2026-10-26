import json,sys,datetime
from pathlib import Path
run=sys.argv[1];b=Path('/data/stage5-v3/ops-competition-2026/pre-attempt04');c=b/'runtime'/run;r=b/'runs'/run
v={'run_id':run,'now':datetime.datetime.now(datetime.timezone.utc).isoformat()}
if (c/'heartbeat.json').exists():
 h=json.loads((c/'heartbeat.json').read_text());v.update(sequence=h['sequence'],heartbeat=h['completed_utc'],refused=h['checks']['collector']['refused'],collector_MiB=round(h['checks']['collector']['working_set_bytes']/1048576,2))
if (c/'workflow.jsonl').exists():v['workflow_tail']=[{k:q[k] for k in ('at_utc','phase','tag','action','error') if k in q} for q in [json.loads(x) for x in (c/'workflow.jsonl').read_text().splitlines()[-3:]]]
if (c/'controller-terminal.json').exists():v['terminal']=json.loads((c/'controller-terminal.json').read_text())
v['chunks']={p.parent.name:json.loads(p.read_text())['status'] for p in r.glob('capture/*/quality.json')}
print(json.dumps(v))
