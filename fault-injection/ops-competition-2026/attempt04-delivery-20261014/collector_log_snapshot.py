import argparse,json,subprocess,hashlib,re
from pathlib import Path
from datetime import datetime,timezone
p=argparse.ArgumentParser();p.add_argument('--label',required=True);a=p.parse_args()
if not re.fullmatch('[A-Za-z0-9-]+',a.label):raise ValueError('LABEL')
root=Path('/data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01-ATTEMPT-04');schedule=json.loads((root/'schedule.json').read_text())
plan=json.loads(Path('/root/ops-competition-2026/attempt04-20261014/plans/SF-PILOT-01-ATTEMPT-04/run-plan.json').read_text())
out=root/'diagnostics';out.mkdir(exist_ok=True);file=out/(a.label+'-collector.log')
if file.exists():raise RuntimeError('NO_OVERWRITE')
cmd=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','-n','otel-demo','logs','otel-collector-845fdc8d87-gk2gh','-c','opentelemetry-collector','--since-time='+schedule['start_utc'],'--timestamps']
x=subprocess.run(cmd,capture_output=True,timeout=30)
if x.returncode:raise RuntimeError(x.stderr.decode())
file.write_bytes(x.stdout);lines=x.stdout.decode().splitlines();errors=[s for s in lines if '\terror\t' in s or '\twarn\t' in s];delivery=[s for s in errors if any(t in s.lower() for t in ('export','retry','refus','timeout','memory'))]
v={'label':a.label,'at_utc':datetime.now(timezone.utc).isoformat(),'source_command':cmd,'file_sha256':hashlib.sha256(x.stdout).hexdigest(),'bytes':len(x.stdout),'log_lines':len(lines),'warn_error_lines':len(errors),'delivery_warn_error_lines':len(delivery),'examples':delivery[:15],'collector_config_sha256':plan['collector_config_sha256']}
with (out/(a.label+'-collector-log-audit.json')).open('x') as f:json.dump(v,f,indent=2)
print(json.dumps(v))
