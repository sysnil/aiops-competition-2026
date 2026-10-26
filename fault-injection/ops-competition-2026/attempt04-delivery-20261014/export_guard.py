"""Bounded post-capture export only. No fault or Kubernetes capability."""
import json,os,shutil,signal,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
BASE=Path('/data/rca-archives/ops-competition-2026/SF-PILOT-01-ATTEMPT-04')
G=1024**3

def size(p):return sum(f.stat().st_size for f in p.rglob('*') if f.is_file()) if p.exists() else 0

def main():
 raw=BASE/'raw';work=BASE/'export-work';public=raw/'public';out=BASE/'export-guard-result.json'
 if out.exists() or public.exists() or work.exists():raise RuntimeError('NO_OVERWRITE')
 assessment=json.loads((raw/'capture-and-effects-assessment.json').read_text())
 if assessment['status']!='CASE_CAPTURE_AND_EFFECTS_PASS':raise RuntimeError('CASE_NOT_QUALIFIED_FOR_EXPORT')
 if shutil.disk_usage('/data').free-4*G<50000000000:raise RuntimeError('PEAK_EXPORT_SPACE')
 env={**os.environ,'PYTHONPATH':'/data/ops-competition-2026/export-runtime/cp312'}
 script=BASE/'code/scripts/pilot_export.py'
 command=['python3','-B',str(script),'--captures',str(raw/'capture'),'--window',str(BASE/'public-window.json'),'--output',str(public),'--work',str(work),'--metric-metadata',str(BASE/'metric-metadata.json')]
 result={'status':'EXPORT_GUARD_FAILED','started_utc':datetime.now(timezone.utc).isoformat(),'command':command,'work_cap_bytes':2*G,'product_cap_bytes':2*G};peak={'work':0,'product':0}
 with (BASE/'export.stdout').open('x') as stdout,(BASE/'export.stderr').open('x') as stderr:
  child=subprocess.Popen(command,stdout=stdout,stderr=stderr,env=env,start_new_session=True)
  try:
   while child.poll() is None:
    w,p=size(work),size(public);peak['work']=max(peak['work'],w);peak['product']=max(peak['product'],p)
    if w>2*G or p>2*G or shutil.disk_usage('/data').free<50000000000:raise RuntimeError('EXPORT_CAPACITY_FLOOR')
    time.sleep(1)
   if child.returncode:raise RuntimeError('EXPORT_EXIT_'+str(child.returncode))
   result['status']='EXPORT_GUARD_PASS'
  except BaseException as e:
   result['error']=str(e)
   if child.poll() is None:
    os.killpg(child.pid,signal.SIGTERM)
    try:child.wait(timeout=15)
    except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
  finally:
   result.update(completed_utc=datetime.now(timezone.utc).isoformat(),peak_bytes=peak,remaining_bytes=shutil.disk_usage('/data').free)
   with out.open('x') as f:json.dump(result,f,indent=2)
 print(json.dumps(result))
 if result['status']!='EXPORT_GUARD_PASS':raise SystemExit(1)
if __name__=='__main__':main()
