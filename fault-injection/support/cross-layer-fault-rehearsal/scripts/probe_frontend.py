"""Bind low-rate direct HTTP probe results to frozen Pod UIDs."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe(pod):
    start = time.monotonic()
    try:
        with urllib.request.urlopen('http://' + pod['pod_ip'] + ':8080/', timeout=2) as response:
            response.read(64)
            return {'status': 'RESPONSE', 'http_status': response.status,
                    'elapsed_ms': (time.monotonic() - start) * 1000}
    except urllib.error.HTTPError as exc:
        return {'status': 'HTTP_ERROR', 'http_status': exc.code,
                'elapsed_ms': (time.monotonic() - start) * 1000}
    except Exception as exc:
        return {'status': 'TIMEOUT' if isinstance(exc, TimeoutError) else 'ERROR',
                'http_status': None, 'elapsed_ms': None,
                'error_type': type(exc).__name__}


def probe_row(pod, index, scheduled):
    observed = datetime.now(timezone.utc).isoformat()
    return {'index': index, 'scheduled_utc':
            datetime.fromtimestamp(scheduled, timezone.utc).isoformat(),
            'observed_utc': observed,
            'pod': pod['name'], 'pod_uid': pod['uid'],
            'node': pod['node'], **probe(pod)}


def run(preflight_path, start_utc, duration, interval, output):
    preflight = json.loads(preflight_path.read_text())
    pods = preflight['targets'] + preflight.get('controls', [])
    if (preflight['status'] != 'RH_PREFLIGHT_READY' or
            {pod['service'] for pod in pods} != {'frontend'} or
            len(pods) != 2 or len({pod['uid'] for pod in pods}) != 2 or
            interval != 5 or duration not in (75, 120) or
            output.exists()):
        raise ValueError('Direct Pod probe needs one fresh frontend rehearsal preflight')
    scheduled_start = datetime.fromisoformat(start_utc.replace('Z', '+00:00')).timestamp()
    if not -2 <= scheduled_start - time.time() <= 180:
        raise ValueError('Probe start is not fresh')
    output.mkdir(parents=True)
    count = duration // interval
    with (output / 'observations.jsonl').open('x') as stream:
        with ThreadPoolExecutor(max_workers=2) as pool:
            for index in range(count):
                scheduled = scheduled_start + index * interval
                if scheduled > time.time():
                    time.sleep(scheduled - time.time())
                rows = list(pool.map(lambda pod: probe_row(pod, index, scheduled), pods))
                for row in rows:
                    stream.write(json.dumps(row) + '\n')
                stream.flush()
    observations = output / 'observations.jsonl'
    manifest = {'status': 'RH_FRONTEND_DIRECT_PROBE_COMPLETE',
                'preflight_sha256': sha256(preflight_path),
                'start_utc': start_utc, 'duration_seconds': duration,
                'interval_seconds': interval, 'samples_per_pod': count,
                'pod_requests_concurrent': True,
                'observations_sha256': sha256(observations)}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preflight', required=True, type=Path)
    parser.add_argument('--start-utc', required=True)
    parser.add_argument('--duration', required=True, type=int)
    parser.add_argument('--interval', type=int, default=5)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.preflight, args.start_utc, args.duration,
                         args.interval, args.output)))


if __name__ == '__main__':
    main()
