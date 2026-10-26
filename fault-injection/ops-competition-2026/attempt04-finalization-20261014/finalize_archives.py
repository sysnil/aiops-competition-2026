"""Append only approved delivery folders to immutable raw replicas, then verify all files."""
import json,pathlib,subprocess,sys,hashlib
sys.path.insert(0,'/root/ops-competition-2026/attempt04-20261014/scripts')
from master_transport import copy,remote,HOSTS
R=pathlib.Path('/data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01-ATTEMPT-04')
I=pathlib.Path('/data/stage5-v3/ops-competition-archive-index/SF-PILOT-01-ATTEMPT-04')
B=pathlib.Path('/data/rca-archives/ops-competition-2026/SF-PILOT-01-ATTEMPT-04')
S=pathlib.Path('/root/ops-competition-2026/scripts/competition_archive.py')
F=pathlib.Path('/root/ops-competition-2026/attempt04-finalization-20261014')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(4*1024**2),b''):h.update(block)
 return h.hexdigest()
def rsync(host,src,dest):
 remote(host,['mkdir','-p',dest])
 subprocess.run(['rsync','-a','-e','ssh -o BatchMode=yes -S /run/ops-a04-ssh/'+host+'.sock',str(src)+'/', 'root@'+HOSTS[host]+':'+str(dest)+'/'],check=True,capture_output=True,timeout=300)
def main():
 p=__import__('argparse').ArgumentParser();p.add_argument('--worker',choices=['w2','w3']);a=p.parse_args()
 if not a.worker:
  initial=json.loads((I/'raw-initial-manifest.json').read_text())
  for item in initial['files']:
   if sha(R/item['path'])!=item['sha256']:raise RuntimeError('ORIGINAL_EVIDENCE_CHANGED:'+item['path'])
  for kind,root in [('raw-final',R),('public-final',R/'public'),('private-final',R/'private'),('finalization',F)]:
   subprocess.run(['python3','-B',str(S),'create','--root',str(root),'--manifest',str(I/(kind+'-manifest.json'))],check=True,capture_output=True)
  print(json.dumps({'original_4999_files_unchanged':True,'final_manifests_created':4}));return
 h=a.worker
 for part in ('public','private','reports'):rsync(h,R/part,B/'raw'/part)
 rsync(h,F,B/'finalization')
 results={}
 for kind,root in [('raw-final',B/'raw'),('public-final',B/'raw/public'),('private-final',B/'raw/private'),('finalization',B/'finalization')]:
  mf=I/(kind+'-manifest.json');copy(h,mf,B/mf.name);out=B/(kind+'-verification.json')
  result=remote(h,['python3','-B',B/'competition_archive.py','verify','--root',root,'--manifest',B/mf.name,'--output',out],timeout=600)
  results[kind]=json.loads(result);copy(h,out,I/(h+'-'+out.name),True);print(json.dumps({'worker':h,'kind':kind,'status':results[kind]['status'],'bytes':results[kind]['bytes']}),flush=True)
 with (I/(h+'-final-archive-result.json')).open('x') as f:json.dump(results,f,indent=2)
if __name__=='__main__':main()
