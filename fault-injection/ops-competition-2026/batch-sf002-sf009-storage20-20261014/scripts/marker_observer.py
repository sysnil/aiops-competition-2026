"""Real live safety observer for metadata-only marker tests; no load/fault/capture capability."""
import argparse,sys,os,time,json
from pathlib import Path
from datetime import datetime,timezone
from run_registry import load_bundle,sha,ROOT
from handshake import publish,atomic
# Same actual safety functions as ATTEMPT04, used without an execution environment.
sys.path.insert(0,'/root/ops-competition-2026/attempt04-20261014/scripts')
from pilot_common import telemetry_state
sys.path.insert(0,str(Path(__file__).resolve().parent))
from competition_observation import safety

def main():
 p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--manifest-sha',required=True);a=p.parse_args();m,plan=load_bundle(a.manifest,a.manifest_sha)
 control=Path(plan['control_root']);atomic(control/'observer-pid.json',{'pid':os.getpid()},True);seq=0;initial=None
 while True:
  began=datetime.now(timezone.utc).isoformat();env=safety(plan['targets_and_siblings']);t=telemetry_state(plan['collector_uid'])
  if initial is None:initial=t['refused']
  if t['refused']!=initial:raise RuntimeError('REFUSED_INCREMENT')
  checks={'environment':env,'collector':{k:t[k] for k in ('uid','config_sha256','refused','working_set_bytes','freshness')},'business_faults_injected':0}
  publish(control,plan,m['plan_sha256'],sha(__file__),seq,began,checks);seq+=1
  time.sleep(15)
if __name__=='__main__':main()
