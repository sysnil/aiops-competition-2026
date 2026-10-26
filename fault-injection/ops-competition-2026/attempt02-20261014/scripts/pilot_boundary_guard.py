"""Private, explicit one-second edge evidence. Never shifts or adds public timestamps."""
import argparse,time,sys
from datetime import timedelta
from pilot_common import *
sys.path.insert(0,'/root/stage5-v3-autonomous-preparation/scripts')
from trace_storage_reader import read_window
from capture_live_c2 import request_json,write_json
def main():
    p=argparse.ArgumentParser();p.add_argument('--side',choices=['start','end'],required=True);a=p.parse_args()
    schedule=read(RUN/'schedule.json');edge=datetime.fromisoformat(schedule[a.side+'_utc'])
    time.sleep(max(0,edge.timestamp()+120-time.time()))
    if time.time()>edge.timestamp()+125:raise RuntimeError('Boundary audit missed registered start')
    start=edge-timedelta(seconds=1) if a.side=='start' else edge
    end=edge if a.side=='start' else edge+timedelta(seconds=1)
    out=RUN/'private-boundary'/a.side
    if out.exists():raise RuntimeError('Never overwrite boundary evidence')
    out.mkdir(parents=True,mode=0o700)
    metadata=read_window(start,end,out,OS,request_json,write_json)
    if time.time()>edge.timestamp()+180:raise RuntimeError('Boundary audit deadline exceeded')
    save(out/'manifest.json',{'status':'PRIVATE_BOUNDARY_SOURCE_CAPTURED','side':a.side,'at_utc':now(),
        'source':metadata,'public_export_allowed':False,'timestamp_adjustment':False,
        'sha256':{str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file()}})
if __name__=='__main__':main()
