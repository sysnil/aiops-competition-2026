"""Low-rate, no-fault frontend request sweep for D04 demand feasibility."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

import yaml


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request(pod, timeout):
    began = time.monotonic()
    try:
        with urllib.request.urlopen('http://' + pod['ip'] + ':8080/', timeout=timeout) as response:
            response.read(64)
            return {'status': 'RESPONSE', 'http_status': response.status,
                    'elapsed_ms': (time.monotonic()-began)*1000}
    except urllib.error.HTTPError as exc:
        return {'status': 'HTTP_ERROR', 'http_status': exc.code,
                'elapsed_ms': (time.monotonic()-began)*1000}
    except Exception as exc:
        return {'status': 'TIMEOUT' if isinstance(exc, TimeoutError) else 'ERROR',
                'http_status': None, 'elapsed_ms': None,
                'error_type': type(exc).__name__}


def run(plan_path, health_path, residue_path, start_utc, output):
    plan = yaml.safe_load(plan_path.read_text())
    health = json.loads(health_path.read_text())
    residue = json.loads(residue_path.read_text())
    now = datetime.now(timezone.utc)
    ages = [(now-datetime.fromisoformat(item['at_utc'])).total_seconds()
            for item in (health, residue)]
    if (plan['status'] != 'PREDECLARED_BEFORE_RH_BUSINESS_LOAD_5RPS' or
            health['status'] != 'STAGE5_BASE_HEALTH_PASS' or
            residue['status'] != 'HOST_RESIDUE_PASS' or
            any(not 0 <= age <= 180 for age in ages) or output.exists()):
        raise ValueError('No-fault load sweep needs fresh clean health and new output')
    data = json.loads(subprocess.check_output(['kubectl','-n','otel-demo','get','pods',
        '-l','opentelemetry.io/name=frontend','-o','json'], text=True, timeout=30))
    pods = []
    for item in data['items']:
        statuses = item['status'].get('containerStatuses', [])
        if len(statuses) != 1 or not statuses[0].get('ready') or statuses[0]['restartCount'] != 0:
            raise ValueError('Frontend replica is not stable')
        pods.append({'name': item['metadata']['name'], 'uid': item['metadata']['uid'],
                     'ip': item['status']['podIP'], 'node': item['spec']['nodeName']})
    if len(pods) != 2 or len({pod['node'] for pod in pods}) != 2:
        raise ValueError('Need two stable frontend replicas')
    pods.sort(key=lambda row: row['node'])
    scheduled = datetime.fromisoformat(start_utc.replace('Z','+00:00')).timestamp()
    if not 0 <= scheduled-time.time() <= 180:
        raise ValueError('Load sweep start is not fresh')
    output.mkdir(parents=True)
    rate = plan['extra_requests_per_second']
    count = plan['maximum_requests']
    errors, completed = 0, 0
    observations = output / 'observations.jsonl'
    with observations.open('x') as stream:
        for index in range(count):
            target_time = scheduled + index/rate
            if target_time > time.time():
                time.sleep(target_time-time.time())
            late = time.time()-target_time
            if late > .5:
                break
            pod = pods[index % 2]
            result = request(pod, plan['request_timeout_seconds'])
            row = {'index': index, 'scheduled_utc': datetime.fromtimestamp(
                target_time,timezone.utc).isoformat(),
                'observed_utc': datetime.now(timezone.utc).isoformat(),
                'pod': pod['name'], 'pod_uid': pod['uid'], 'node': pod['node'],
                'schedule_lag_seconds': late, **result}
            stream.write(json.dumps(row)+'\n')
            stream.flush()
            completed += 1
            if result['http_status'] != 200:
                errors += 1
            if errors > plan['maximum_non_200_or_timeout']:
                break
    manifest = {'status': 'RH_LOAD_SWEEP_PASS' if completed == count and errors <=
                plan['maximum_non_200_or_timeout'] else 'RH_LOAD_SWEEP_INCOMPLETE',
                'start_utc': start_utc,
                'end_utc': datetime.fromtimestamp(scheduled+plan['duration_seconds'],
                                                  timezone.utc).isoformat(),
                'planned_requests': count, 'completed_requests': completed,
                'non_200_or_timeout': errors,
                'plan_sha256': sha256(plan_path),
                'health_sha256': sha256(health_path),
                'residue_sha256': sha256(residue_path),
                'observations_sha256': sha256(observations),
                'fault_injected': False, 'pods': pods}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--plan',required=True,type=Path)
    parser.add_argument('--health',required=True,type=Path)
    parser.add_argument('--residue',required=True,type=Path)
    parser.add_argument('--start-utc',required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=run(args.plan,args.health,args.residue,args.start_utc,args.output)
    print(json.dumps({'status':result['status'],'completed':result['completed_requests'],
                      'errors':result['non_200_or_timeout']}),flush=True)
    if result['status']!='RH_LOAD_SWEEP_PASS':
        raise SystemExit(1)


if __name__=='__main__':
    main()
