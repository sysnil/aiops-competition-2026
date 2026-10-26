"""Private, explicit one-second edge evidence. Never shifts or adds public timestamps."""
import argparse,time,sys
from datetime import timedelta
from pilot_common import *
sys.path.insert(0,'/root/ops-competition-2026/attempt04-20261014/capture')
from trace_storage_reader import read_window,index_names,digest
from boundary_empty import verified_empty
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
    # The auxiliary window is strictly outside public data. Its absence cannot explain
    # any missing request; all final identity checks still reject unexplained gaps.
    indices=index_names(start,end);index_path=','.join(indices)
    settings=request_json(OS+'/'+index_path+'/_settings?filter_path=*.settings.index.uuid')
    mappings=request_json(OS+'/'+index_path+'/_mapping')
    if set(settings)!=set(indices) or set(mappings)!=set(indices):raise RuntimeError('Boundary source index mismatch')
    start_us=int(start.timestamp()*1000000);end_us=int(end.timestamp()*1000000)
    query={'size':0,'track_total_hits':True,'query':{'bool':{'filter':[
        {'range':{'startTimeMillis':{'gte':start_us//1000,'lt':(end_us+999)//1000}}},
        {'range':{'startTime':{'gte':start_us,'lt':end_us}}}]}}}
    response=request_json(OS+'/'+index_path+'/_search',json.dumps(query).encode())
    write_json(out/'auxiliary-count-query.json',{'query':query,'response':response})
    if verified_empty(response):
        after=request_json(OS+'/'+index_path+'/_settings?filter_path=*.settings.index.uuid')
        if settings!=after:raise RuntimeError('Boundary index changed')
        metadata={'trace_source':'JAEGER_OPENSEARCH_STORAGE','indices':{n:{'uuid':settings[n]['settings']['index']['uuid'],'mapping_sha256':digest(mappings[n]['mappings'])} for n in indices},
            'query_start_utc':start.isoformat(),'query_end_utc':end.isoformat(),'query_sha256':digest(query),
            'total_storage_hits':0,'unique_spans':0,'unique_traces':0,'raw_pages':0,'auxiliary_outside_window_empty':True}
        write_json(out/'trace-storage-source.json',metadata)
    else:
        metadata=read_window(start,end,out,OS,request_json,write_json)
    if time.time()>edge.timestamp()+180:raise RuntimeError('Boundary audit deadline exceeded')
    save(out/'manifest.json',{'status':'PRIVATE_BOUNDARY_SOURCE_CAPTURED','side':a.side,'at_utc':now(),
        'source':metadata,'public_export_allowed':False,'timestamp_adjustment':False,
        'sha256':{str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file()}})
if __name__=='__main__':main()
