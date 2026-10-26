#!/usr/bin/env python3
"""Verify registered design, six attempts, cleanup and independent archive hashes."""

import argparse
import hashlib
import json
from pathlib import Path


ROOT=Path('/root/cross-layer-observability-control-rehearsal')
STAGE5=Path('/root/stage5-v2-cross-layer-rca/freeze-manifest.json')
EXPECTED_STAGE5='6ebafa764b904950c00904d06cebae68c66727a8cf703158aa259bb728cd6452'


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def read(path):return json.loads(path.read_text())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--final-health',type=Path,required=True)
    parser.add_argument('--final-residue',type=Path,required=True)
    args=parser.parse_args()
    registered=read(ROOT/'design/registered-rehearsal-manifest.json')
    checks={'stage5_v2_freeze_unchanged':sha(STAGE5)==EXPECTED_STAGE5,
        'registered_sources_unchanged':all(sha(ROOT/path)==expected
            for path,expected in registered['hashes'].items()),
        'final_health':read(args.final_health)['status']=='STAGE5_BASE_HEALTH_PASS',
        'final_five_host_residue':read(args.final_residue)['status']=='HOST_RESIDUE_PASS'}
    attempts={}
    for ident in registered['ids']:
        attempt=ROOT/'attempts'/ident
        result=read(attempt/'result.json')
        state=read(attempt/'state.json')
        paired=read(attempt/'load_pair/result.json')
        machine=read(attempt/'load_pair/state-machine/result-manifest.json')
        controls=read(attempt/'load_pair/controls/result-manifest.json')
        control_hashes={name:sha(attempt/'load_pair/controls'/(name+'.jsonl'))==row['requests_sha256']
            for name,row in controls['streams'].items()}
        capture=read(attempt/'captures-retrospective/capture-manifest.json')
        phase_hashes={phase:all(sha(attempt/'captures-retrospective'/phase/filename)==data[key]
            for filename,key in [('prometheus.json','prometheus_sha256'),
                                 ('opensearch-logs.jsonl','logs_sha256'),
                                 ('jaeger-storage-spans.jsonl','spans_sha256')])
            for phase,data in capture['phases'].items()}
        route_ok=True
        if ident.startswith(('OC-D11','OC-D12')):
            pre=read(attempt/'preflight.json')
            route=ROOT/'observations'/(ident+'-route-proof.json')
            route_ok=sha(route)==pre['route_proof_sha256']
        attempts[ident]={'complete':result['status']=='REHEARSAL_COMPLETE' and
            paired['status']=='LOAD_PAIR_COMPLETE' and state['status']=='CLEANED',
            'state_machine_hash':sha(attempt/'load_pair/state-machine/sessions.jsonl')==machine['sessions_sha256'],
            'control_hashes':control_hashes,'capture_hashes':phase_hashes,
            'route_proof_hash':route_ok,
            'uid_coverage':read(attempt/'identity-audit/audit.json')['uid_coverage_fraction']}
    checks['all_six_complete']=len(attempts)==6 and all(row['complete'] for row in attempts.values())
    checks['all_run_and_capture_hashes']=all(row['state_machine_hash'] and
        all(row['control_hashes'].values()) and all(row['capture_hashes'].values()) and
        row['route_proof_hash'] for row in attempts.values())
    checks['all_uid_coverage_at_least_99_percent']=all(row['uid_coverage']>=.99
                                                        for row in attempts.values())
    index={}
    for path in sorted(ROOT.rglob('*')):
        if path.is_file() and path.name not in ('archive-integrity.json','sha256-manifest.json'):
            index[str(path.relative_to(ROOT))]={'sha256':sha(path),'bytes':path.stat().st_size}
    result={'checks':checks,'attempts':attempts,'indexed_files':len(index),
            'stage5_v2_freeze_sha256':sha(STAGE5)}
    (ROOT/'archive-integrity.json').write_text(json.dumps(result,indent=2)+'\n')
    (ROOT/'sha256-manifest.json').write_text(json.dumps(index,indent=2)+'\n')
    print(json.dumps({'checks':checks,'indexed_files':len(index)}))
    if not all(checks.values()):raise SystemExit(1)


if __name__=='__main__':main()
