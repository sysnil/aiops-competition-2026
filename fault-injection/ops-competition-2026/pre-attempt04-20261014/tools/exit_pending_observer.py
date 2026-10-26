"""External crash stimulus for a dry-run observer after BOTH timers pass live preflight."""
import json,time,sys,subprocess
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path('/root/ops-competition-2026/pre-attempt04-20261014');sys.path.insert(0,str(ROOT/'scripts'))
from run_registry import load_bundle,sha
run=sys.argv[1];mp=ROOT/'plans'/run/'run-manifest.json';m,p=load_bundle(mp,sha(mp));c=Path(p['control_root'])
c.mkdir(parents=True,exist_ok=True)
with (c/'exit-stimulus-registration.json').open('x') as f:json.dump({'run_id':run,'stimulus':'SIGTERM to own dry observer only after ARMED preflight passes','business_faults_injected':0,'expected':'OBSERVER_ABORT plus both pending timers cancelled'},f,indent=2)
deadline=time.monotonic()+100
while time.monotonic()<deadline:
    if (c/'workflow.jsonl').exists():
        rows=[json.loads(l) for l in (c/'workflow.jsonl').read_text().splitlines()]
        if any(r['phase']=='PREFLIGHT_PASS' and r.get('tag')=='ARMED' for r in rows):break
    time.sleep(.1)
else:raise RuntimeError('No verified armed state; no stimulus sent')
event={'at_utc':datetime.now(timezone.utc).isoformat(),'signal':'SIGTERM','observer_unit':m['observer_unit'],'business_faults_injected':0}
subprocess.run(['systemctl','kill','--kill-who=main','--signal=SIGTERM',m['observer_unit']+'.service'],check=True)
with (c/'external-observer-exit.json').open('x') as f:json.dump(event,f,indent=2)
print(json.dumps(event))
