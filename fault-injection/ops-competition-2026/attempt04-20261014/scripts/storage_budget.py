"""Peak simultaneous occupancy; unchanged safety floors, no deletion/compression assumption."""
import json,shutil,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
from master_transport import remote
G=1024**3
ROOT_FLOOR=46000000000
WORKER_FLOOR=50000000000
CAP=24*G

def size(root):return sum(p.stat().st_size for p in Path(root).rglob('*') if p.is_file() and not p.is_symlink()) if Path(root).exists() else 0

def budget():
    master={'root_free':shutil.disk_usage('/').free,'data_free':shutil.disk_usage('/data/stage5-v3').free}
    workers={h:json.loads(remote(h,['python3','-c','import shutil,json; print(json.dumps({"root_free":shutil.disk_usage("/").free,"data_free":shutil.disk_usage("/data").free}))'])) for h in ('w2','w3')}
    # Raw JSON reservation 24 GiB + other raw/control 2 GiB. Worker2 permanently
    # archives once, exports from that copy. NO second input copy or local tar.
    demand={'master_data':34*G,'master_root':64*1024**2,'w2_data':31*G,'w3_data':29*G}
    checks={'master_root_peak_reserve':master['root_free']-demand['master_root']>=ROOT_FLOOR,
            'master_data_peak_reserve':master['data_free']-demand['master_data']>=64*G,
            **{h+'_data_peak_reserve':v['data_free']-demand[h+'_data']>=WORKER_FLOOR for h,v in workers.items()},
            **{h+'_root_reserve':v['root_free']-64*1024**2>=WORKER_FLOOR for h,v in workers.items()}}
    return {'status':'STORAGE_PEAK_BUDGET_PASS' if all(checks.values()) else 'STORAGE_PEAK_BUDGET_BLOCKED','at_utc':datetime.now(timezone.utc).isoformat(),
      'master':master,'workers':workers,'peak_incremental_bytes':demand,'checks':checks,'capture_json_cap_bytes':CAP,'raw_plus_control_cap_bytes':26*G,
      'worker2_export_temporary_cap_bytes':2*G,'public_private_product_cap_bytes':2*G,'operational_reserve_bytes':G,
      'worker_safety_floor_bytes':WORKER_FLOOR,'master_root_floor_bytes':ROOT_FLOOR,'master_data_floor_bytes':64*G,
      'prior_full02_raw_bytes':3192086230,'max_prior_five_minute_all_versions_bytes':984513796,'hour_base_from_chunk_max_bytes':12*984513796,
      'json_budget_factor_over_chunk_peak_projection':CAP/(12*984513796),'archive_strategy':'one permanent uncompressed raw replica per Worker; Worker2 export reads its archive; no additional raw staging/tar',
      'abort_residue_policy':'keep all existing files; reservation cap fails closed before further writes; no deletion',
      'protected_archives':'all retained; existing usage already deducted from available space'}

def runtime_check(raw,control):
    actual=size(raw)+size(control)
    if actual>26*G:raise RuntimeError('RAW_CONTROL_BUDGET_EXCEEDED')
    if shutil.disk_usage('/').free<ROOT_FLOOR:raise RuntimeError('MASTER_ROOT_FLOOR')
    if shutil.disk_usage('/data/stage5-v3').free<64*G:raise RuntimeError('MASTER_DATA_FLOOR')
    return {'raw_control_bytes':actual,'root_free_bytes':shutil.disk_usage('/').free}
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();v=budget()
    with Path(a.output).open('x') as f:json.dump(v,f,indent=2)
    print(json.dumps(v))
    if v['status']!='STORAGE_PEAK_BUDGET_PASS':raise SystemExit(1)
