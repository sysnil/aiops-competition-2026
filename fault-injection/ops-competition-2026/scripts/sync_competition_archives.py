#!/usr/bin/env python3
"""Stream immutable evidence to both Workers without a Mac raw-data copy."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
from run_competition_qualification import ROOT,remote,ssh,copy,LOCAL

def transfer(worker,source,destination):
    reader=subprocess.Popen(ssh('master',['cat',source]),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    writer=subprocess.Popen(ssh(worker,['tee',destination]),stdin=reader.stdout,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    reader.stdout.close();_,e=writer.communicate(timeout=120);_,se=reader.communicate(timeout=30)
    if writer.returncode or reader.returncode:raise RuntimeError('Metadata transfer failed: '+(se+e).decode()[-500:])

def main():
    p=argparse.ArgumentParser();p.add_argument('--id',required=True);a=p.parse_args()
    if not a.id.startswith('SF01-') or not a.id.replace('-','').isalnum():raise RuntimeError('Invalid snapshot name')
    local=LOCAL/'archive-index'/a.id
    if local.exists():raise RuntimeError('Never overwrite archive attempt')
    local.mkdir(parents=True,mode=0o700)
    index='/data/stage5-v3/ops-competition-archive-index/'+a.id
    remote('master',['mkdir','-p',index])
    sources={'raw':'/data/stage5-v3/ops-competition-2026','control':ROOT};proofs={}
    script=ROOT+'/scripts/competition_archive.py'
    for kind,source in sources.items():
        result=subprocess.run(ssh('master',['lsof','-nP','+D',source]),capture_output=True,text=True,timeout=120)
        if result.returncode not in (0,1) or len(result.stdout.splitlines())>1:raise RuntimeError('Source still has active writer or reader')
        manifest=index+'/'+kind+'-manifest.json'
        remote('master',['python3','-B',script,'create','--root',source,'--manifest',manifest],timeout=600)
        item=json.loads(remote('master',['cat',manifest]));copy('master',manifest,local/(kind+'-manifest.json'),True)
        for worker in ('w2','w3'):
            root='/data/rca-archives/ops-competition-2026/'+a.id
            destination=root+'/'+kind
            remote(worker,['python3','-c',
                'import pathlib,shutil; p=pathlib.Path('+repr(destination)+'); assert not p.exists(); '
                'assert shutil.disk_usage("/data").free-'+str(item['bytes'])+'>=50000000000; p.mkdir(parents=True,mode=0o700)'])
            reader=subprocess.Popen(ssh('master',['tar','-I','gzip -1','-C',source,'-cf','-','.']),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            writer=subprocess.Popen(ssh(worker,['tar','-C',destination,'-xzf','-']),stdin=reader.stdout,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            reader.stdout.close();_,e=writer.communicate(timeout=1800);_,se=reader.communicate(timeout=30)
            if reader.returncode or writer.returncode:raise RuntimeError('Raw archive transfer failed: '+(se+e).decode()[-500:])
            wm=root+'/'+kind+'-manifest.json';wp=root+'/'+kind+'-verification.json';transfer(worker,manifest,wm)
            copy(worker,LOCAL/'scripts/competition_archive.py',root+'/competition_archive.py')
            verdict=remote(worker,['python3','-B',root+'/competition_archive.py','verify','--root',destination,
                '--manifest',wm,'--output',wp],timeout=600)
            proof=json.loads(verdict);proofs[kind+'-'+worker]=proof
            copy(worker,wp,local/(kind+'-'+worker+'-verification.json'),True)
            copy('master',local/(kind+'-'+worker+'-verification.json'),index+'/'+kind+'-'+worker+'-verification.json')
            print(json.dumps({'kind':kind,'worker':worker,'status':proof['status'],'bytes':proof['bytes']}),flush=True)
    value={'status':'COMPETITION_DUAL_ARCHIVE_PASS','id':a.id,'proofs':proofs,'at_utc':datetime.now(timezone.utc).isoformat(),
        'archive_index':index,'large_raw_data_copied_to_mac':False}
    with (local/'result.json').open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    copy('master',local/'result.json',index+'/result.json');print(json.dumps(value),flush=True)

if __name__=='__main__':main()
