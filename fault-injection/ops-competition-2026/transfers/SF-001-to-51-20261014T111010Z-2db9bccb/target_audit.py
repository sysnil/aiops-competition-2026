import pathlib,json,stat,subprocess,hashlib,os,shutil
import yaml
r=pathlib.Path('/data/aiops-contest');staging=r/'staging';final=staging/'SF-001-incoming'
mount=json.loads(subprocess.check_output(['findmnt','-J','-T',str(staging),'-o','TARGET,SOURCE,FSTYPE,UUID,OPTIONS'],text=True))['filesystems'][0]
assert mount['target']=='/data' and mount['source']=='/dev/sdb1' and mount['fstype']=='ext4' and mount['uuid']=='b54b1161-c8ef-43d2-b883-c691824bd84c'
assert 'rw' in mount['options'].split(',') and os.stat(staging).st_dev!=os.stat('/').st_dev
assert not any(p.is_symlink() for p in (r,staging)) and staging.stat().st_uid==0 and stat.S_IMODE(staging.stat().st_mode)==0o700
pid=int(subprocess.check_output(['systemctl','show','aiops-contest-api.service','-p','MainPID','--value'],text=True))
env=pathlib.Path('/proc',str(pid),'environ').read_bytes().split(b'\0')
config=next((s.split(b'=',1)[1].decode() for s in env if s.startswith(b'AIOPS_CONFIG=')),'/opt/aiops-contest-server/config/config.yaml')
setting=yaml.safe_load(pathlib.Path(config).read_text())['data']
assert pathlib.Path(setting['staging_root']).resolve()==staging
assert not staging.is_relative_to(pathlib.Path(setting['public_root']).resolve())
assert not staging.is_relative_to(pathlib.Path(setting['mock_root']).resolve())
config_files=[pathlib.Path(config),pathlib.Path('/etc/systemd/system/aiops-contest-api.service'),pathlib.Path('/etc/nginx/nginx.conf')]
config_files+=list(pathlib.Path('/etc/nginx/sites-enabled').glob('*'))+list(pathlib.Path('/opt/aiops-contest-server/app').glob('*.py'))
files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in config_files if p.is_file()}
indexes={str(p):{'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns,'inode':p.stat().st_ino} for p in (r/'indexes').rglob('*') if p.is_file()}
units={u:subprocess.check_output(['systemctl','show',u,'-p','ActiveState','-p','MainPID','-p','ExecMainStartTimestampMonotonic','-p','User'],text=True) for u in ['aiops-contest-api.service','nginx.service']}
assert shutil.disk_usage(staging).free>2*77945722+1024**3
print(json.dumps({'status':'TARGET_READONLY_LAYOUT_AND_ACCESS_PASS','mount':mount,'data_disk_free_bytes':shutil.disk_usage(staging).free,
 'root_free_bytes':shutil.disk_usage('/').free,'staging_mode':oct(stat.S_IMODE(staging.stat().st_mode)),'staging_owner':'root:root',
 'final_exists':final.exists(),'public_case_exists':(r/'public/cases/SF-001').exists(),'published_manifest_exists':(r/'public/manifests/SF-001.json').exists(),
 'api_data_roots':{k:setting[k] for k in ['public_root','mock_root','staging_root','index_root']},'units':units,'config_code_sha256':files,'index_file_stat_only':indexes,
 'listeners':subprocess.check_output(['ss','-ltnp'],text=True),'staging_existing_entries':sorted(p.name for p in staging.iterdir())}))
