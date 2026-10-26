"""Verify every rehearsal capture, probe, cleanup record, and source digest."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


CASES = ('RH-SVC-PULSE-3S', 'RH-SVC-PULSE-5S', 'RH-POD-QUOTA',
         'RH-POD-PULSE', 'RH-NET-LOSS-10', 'RH-NET-BW-CONTROLS')
FROZEN_SHA = '6ebafa764b904950c00904d06cebae68c66727a8cf703158aa259bb728cd6452'


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def check_probe(directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    file = directory / 'observations.jsonl'
    if sha256(file) != manifest['observations_sha256']:
        raise ValueError('Probe SHA mismatch: ' + str(file))
    rows = [json.loads(line) for line in file.read_text().splitlines() if line]
    if manifest['status'] == 'RH_NETWORK_PROBES_COMPLETE':
        actual = [row['index'] for row in rows]
        if actual != list(range(manifest['samples'])):
            raise ValueError('Network probe has missing/duplicate indices')
        for row in rows:
            iperf = row['iperf3']
            if iperf['status'] != 'PASS' and iperf['throughput_bps'] is not None:
                raise ValueError('Failed probe has fabricated throughput')
    elif manifest['status'] == 'RH_FRONTEND_DIRECT_PROBE_COMPLETE':
        expected = manifest['samples_per_pod']
        for pod in {row['pod'] for row in rows}:
            if [row['index'] for row in rows if row['pod'] == pod] != list(range(expected)):
                raise ValueError('Pod probe has missing/duplicate indices')
    elif manifest['status'] == 'RH_CGROUP_SAMPLE_COMPLETE':
        if [row['index'] for row in rows] != list(range(manifest['samples'])):
            raise ValueError('Cgroup probe has missing/duplicate indices')
    else:
        raise ValueError('Unknown rehearsal probe manifest')
    return {'samples': len(rows), 'manifest_sha256': sha256(directory / 'manifest.json')}


def verify(root, frozen_root):
    if sha256(frozen_root / 'freeze-manifest.json') != FROZEN_SHA:
        raise ValueError('Frozen Stage5-v2 manifest hash changed')
    sys.path.insert(0, '/root/stage4f-cross-layer-validation/scripts')
    from convert_live import load_capture

    attempts = {}
    for case in CASES:
        directory = root / 'attempts' / case
        captures = {}
        for phase in ('before', 'during', 'after'):
            item = load_capture(directory / 'capture' / phase, phase)
            captures[phase] = {'manifest_sha256': sha256(
                directory / 'capture' / phase / 'capture-manifest.json'),
                'traces': item['manifest']['trace_records'],
                'logs': item['manifest']['log_records']}
        probes = {}
        for manifest in directory.rglob('manifest.json'):
            if 'capture' in manifest.parts:
                continue
            if manifest.parent.name in ('before', 'during', 'after') or (
                    'cgroup' in manifest.parts):
                if (manifest.parent / 'observations.jsonl').exists():
                    probes[str(manifest.parent.relative_to(directory))] = check_probe(
                        manifest.parent)
        states = {}
        for state_file in directory.glob('worker*-state.json'):
            state = json.loads(state_file.read_text())
            if state['status'] != 'CLEANED':
                raise ValueError('Rehearsal state was not cleaned: ' + str(state_file))
            states[state_file.name] = sha256(state_file)
        if not states or not probes:
            raise ValueError('Rehearsal lacks cleanup state or probe archive: ' + case)
        attempts[case] = {'captures': captures, 'probes': probes, 'states': states}
    residue = json.loads((root / 'health/final-residue.json').read_text())
    health = json.loads((root / 'health/final-health.json').read_text())
    if residue['status'] != 'HOST_RESIDUE_PASS' or (
            health['status'] != 'STAGE5_BASE_HEALTH_PASS' or
            health['workload_nonzero_failures_2m'] != 0):
        raise ValueError('Final recovery/residue gate failed')
    load_sweep = None
    load_dir = root / 'attempts/RH-BUSINESS-LOAD-5RPS'
    if load_dir.exists():
        manifest = json.loads((load_dir / 'manifest.json').read_text())
        observations = load_dir / 'observations.jsonl'
        rows = [json.loads(line) for line in observations.read_text().splitlines() if line]
        if (manifest['status'] != 'RH_LOAD_SWEEP_PASS' or
                manifest['fault_injected'] is not False or
                sha256(observations) != manifest['observations_sha256'] or
                [row['index'] for row in rows] != list(range(manifest['planned_requests']))):
            raise ValueError('No-fault business-load sweep is incomplete')
        recovery = json.loads((root / 'health/load-recovery-health.json').read_text())
        post_residue = json.loads((root / 'health/load-recovery-residue.json').read_text())
        if (recovery['status'] != 'STAGE5_BASE_HEALTH_PASS' or
                recovery['workload_nonzero_failures_2m'] != 0 or
                post_residue['status'] != 'HOST_RESIDUE_PASS'):
            raise ValueError('No-fault load sweep recovery gate failed')
        load_sweep = {'status': manifest['status'], 'requests': len(rows),
                      'manifest_sha256': sha256(load_dir / 'manifest.json'),
                      'recovery_health_sha256': sha256(root / 'health/load-recovery-health.json')}
    source_hashes = {str(path.relative_to(root)): sha256(path)
                     for directory in ('design', 'scripts')
                     for path in sorted((root / directory).iterdir()) if path.is_file()}
    return {'status': 'RH_ARCHIVE_VERIFIED',
            'verified_at_utc': datetime.now(timezone.utc).isoformat(),
            'frozen_stage5_v2_sha256': FROZEN_SHA,
            'source_hashes': source_hashes, 'attempts': attempts,
            'load_sweep': load_sweep,
            'final_health_sha256': sha256(root / 'health/final-health.json'),
            'final_residue_sha256': sha256(root / 'health/final-residue.json')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--frozen-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Verification output already exists')
    result = verify(args.root, args.frozen_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'],
                      'attempts': len(result['attempts']),
                      'source_files': len(result['source_hashes'])}))


if __name__ == '__main__':
    main()
