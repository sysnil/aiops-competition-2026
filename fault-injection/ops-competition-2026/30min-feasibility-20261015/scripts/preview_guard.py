"""Bounded internal no-fault preview export. No fault or Kubernetes capability."""
import json,os,shutil,signal,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
BASE=Path('/data/rca-archives/ops-competition-2026/SF30-NOFAULT-20261015')
G=1024**3

def size(p):return sum(f.stat().st_size for f in p.rglob('*') if f.is_file()) if p.exists() else 0

def main():
 raw=BASE/'raw';work=BASE/'preview-work';public=BASE/'preview';out=BASE/'preview-guard-result.json'
 if out.exists() or public.exists() or work.exists():raise RuntimeError('NO_OVERWRITE')
 assessment=json.loads((raw/'terminal.json').read_text())
 if assessment['status']!='NOFAULT_30MIN_CAPTURE_PASS':raise RuntimeError('NOFAULT_CAPTURE_NOT_VERIFIED')
 if shutil.disk_usage('/data').free-4*G<20000000000:raise RuntimeError('PEAK_EXPORT_SPACE')
 env={**os.environ,'PYTHONPATH':'/data/ops-competition-2026/export-runtime/cp312'}
 script=BASE/'preview-tools/preview_export.py'
 command=['python3','-B',str(script),'--captures',str(raw/'capture'),'--window',str(BASE/'public-window.json'),'--output',str(public),'--work',str(work),'--metric-metadata',str(BASE/'metric-metadata.json')]
 result={'status':'NOFAULT_PREVIEW_EXPORT_FAILED','started_utc':datetime.now(timezone.utc).isoformat(),'command':command,'work_cap_bytes':2*G,'product_cap_bytes':2*G};peak={'work':0,'product':0}
 with (BASE/'export.stdout').open('x') as stdout,(BASE/'export.stderr').open('x') as stderr:
  child=subprocess.Popen(command,stdout=stdout,stderr=stderr,env=env,start_new_session=True)
  try:
   while child.poll() is None:
    w,p=size(work),size(public);peak['work']=max(peak['work'],w);peak['product']=max(peak['product'],p)
    if w>2*G or p>2*G or shutil.disk_usage('/data').free<20000000000:raise RuntimeError('EXPORT_CAPACITY_FLOOR')
    time.sleep(1)
   if child.returncode:raise RuntimeError('EXPORT_EXIT_'+str(child.returncode))
   result['status']='NOFAULT_PREVIEW_EXPORT_PASS'
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
 if result['status']!='NOFAULT_PREVIEW_EXPORT_PASS':raise SystemExit(1)
if __name__=='__main__':main()
