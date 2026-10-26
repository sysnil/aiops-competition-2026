"""External no-fault observer crash stimulus; the controller must detect it itself."""
import json,time,subprocess
from pathlib import Path
from datetime import datetime,timezone
P=Path('/root/ops-competition-2026/plans/A03-OBSERVER-EXIT-TEST.json')
C=Path('/data/stage5-v3/ops-competition-2026/attempt03-control/A03-OBSERVER-EXIT-TEST')
plan=json.loads(P.read_text())
assert plan['mode']=='EXIT_TEST' and plan['faults']==[]
start=datetime.fromisoformat(plan['start_utc']).timestamp()
registration={'run_id':plan['id'],'stimulus':'external SIGTERM to real observer only','offset_seconds':30,'faults_injected':0,'expected_detection':['OBSERVER_ABORT','OBSERVER_EXITED'],'expected_pending_markers_cancelled':2}
with (C/'external-exit-registration.json').open('x') as f:json.dump(registration,f,indent=2)
while time.time()<start+30:time.sleep(min(.2,max(0,start+30-time.time())))
receipt=json.loads((C/'ready-receipt.json').read_text());assert receipt['run_id']==plan['id']
result={'at_utc':datetime.now(timezone.utc).isoformat(),'signal':'SIGTERM','observer_unit':plan['observer_unit'],'faults_injected':0}
subprocess.run(['systemctl','kill','--kill-who=main','--signal=SIGTERM',plan['observer_unit']+'.service'],check=True)
with (C/'external-observer-exit.json').open('x') as f:json.dump(result,f,indent=2)
print(json.dumps(result))
