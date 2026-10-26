"""Copy one immutable evidence stage directly Master -> Worker; no Mac raw cache."""
import argparse,json,sys,subprocess,shlex
from pathlib import Path
sys.path.insert(0,'/root/ops-competition-2026/attempt04-20261014/scripts')
from master_transport import remote,copy,ssh
BASE=Path('/data/rca-archives/ops-competition-2026/SF-PILOT-01-ATTEMPT-04')
INDEX=Path('/data/stage5-v3/ops-competition-archive-index/SF-PILOT-01-ATTEMPT-04')
SCRIPT=Path('/root/ops-competition-2026/scripts/competition_archive.py')
def main():
 p=argparse.ArgumentParser();p.add_argument('--stage',required=True,choices=['raw','runtime','code','delivery']);p.add_argument('--source',type=Path,required=True);p.add_argument('--worker',required=True,choices=['w2','w3']);a=p.parse_args()
 if a.source.is_symlink():raise RuntimeError('SOURCE_SYMLINK')
 INDEX.mkdir(parents=True,exist_ok=True,mode=0o700);manifest=INDEX/(a.stage+'-initial-manifest.json')
 if not manifest.exists():subprocess.run(['python3','-B',str(SCRIPT),'create','--root',str(a.source),'--manifest',str(manifest)],check=True,capture_output=True)
 content=json.loads(manifest.read_text());dest=BASE/a.stage
 remote(a.worker,['python3','-c','import pathlib,shutil; p=pathlib.Path('+repr(str(dest))+'); assert not p.exists(); assert shutil.disk_usage("/data").free-'+str(content['bytes'])+'>=50000000000; p.mkdir(parents=True,mode=0o700)'])
 # Tar is streamed; never materialize a second archive file on either host.
 reader=subprocess.Popen(['tar','-I','gzip -1','-C',str(a.source),'-cf','-','.'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 writer=subprocess.Popen(ssh(a.worker,['tar','-C',dest,'-xzf','-']),stdin=reader.stdout,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 reader.stdout.close();_,err=writer.communicate(timeout=1800);_,err2=reader.communicate(timeout=30)
 if writer.returncode or reader.returncode:raise RuntimeError('COPY_FAILED:'+repr(err+err2))
 copy(a.worker,manifest,BASE/manifest.name);copy(a.worker,SCRIPT,BASE/'competition_archive.py')
 output=BASE/(a.stage+'-initial-verification.json')
 result=remote(a.worker,['python3','-B',BASE/'competition_archive.py','verify','--root',dest,'--manifest',BASE/manifest.name,'--output',output],timeout=600)
 copy(a.worker,output,INDEX/(a.worker+'-'+output.name),True)
 print(result)
if __name__=='__main__':main()
