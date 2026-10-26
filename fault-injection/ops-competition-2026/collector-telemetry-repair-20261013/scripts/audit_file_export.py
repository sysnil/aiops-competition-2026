"""Bounded read-only scan of one closed Collector archive, with exact request window."""
import argparse,json,hashlib,subprocess
from pathlib import Path
from datetime import datetime,timezone
from collections import defaultdict
def main():
    p=argparse.ArgumentParser();p.add_argument('--requests',type=Path,required=True);p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists() or not a.archive.name.endswith('-size.zst'):raise RuntimeError('Only closed original file and new output')
    request=json.loads(a.requests.read_text());ids={r['trace_id'] for r in request['rows']}
    lo=int(datetime.fromisoformat(request['window']['start']).timestamp()*1e9);hi=int(datetime.fromisoformat(request['window']['end']).timestamp()*1e9)
    before=a.archive.stat();matches=defaultdict(list);lines=0;minimum=None;maximum=None
    with subprocess.Popen(['zstdcat',str(a.archive)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) as process:
        for line in process.stdout:
            lines+=1
            if not any(tid in line for tid in ids):continue
            record=json.loads(line)
            for resource in record.get('resourceSpans',[]):
                attrs={r['key']:r['value'] for r in resource['resource'].get('attributes',[])}
                service=attrs.get('service.name',{}).get('stringValue')
                for scope in resource.get('scopeSpans',[]):
                    for span in scope.get('spans',[]):
                        tid=span.get('traceId');stamp=int(span.get('startTimeUnixNano',0))
                        if tid in ids and lo<=stamp<hi:matches[tid].append({'span_id':span.get('spanId'),'service':service,'kind':span.get('kind'),'start_ns':stamp})
        error=process.stderr.read();code=process.wait()
    after=a.archive.stat()
    if code or before.st_size!=after.st_size or before.st_mtime_ns!=after.st_mtime_ns:raise RuntimeError('Incomplete scan or file mutated: '+error[-300:])
    h=hashlib.sha256()
    with a.archive.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    rows=[{'trace_id':tid,'stored_file_spans':matches[tid],
        'proxy_server_spans':sum(s['service']=='frontend-proxy' and s['kind']==2 for s in matches[tid])} for tid in sorted(ids)]
    result={'status':'CLOSED_FILE_EXPORT_SCAN_COMPLETE','at_utc':datetime.now(timezone.utc).isoformat(),'source':str(a.archive),
        'source_sha256':h.hexdigest(),'source_bytes':before.st_size,'source_inode':before.st_ino,'lines_scanned':lines,'rows':rows,
        'requests_with_any_file_span':sum(bool(r['stored_file_spans']) for r in rows),
        'requests_with_proxy_file_span':sum(r['proxy_server_spans']>0 for r in rows),'source_modified':False}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({k:result[k] for k in ['status','lines_scanned','requests_with_any_file_span','requests_with_proxy_file_span']}))
if __name__=='__main__':main()
