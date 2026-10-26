#!/usr/bin/env python3
"""Read public Final task titles through the organizer's gated preview identity."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from fastapi.testclient import TestClient


os.environ.setdefault("AIOPS_CONFIG", "/etc/aiops-contest/config.yaml")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app  # noqa: E402


def main() -> None:
    bundle = json.loads(Path('/root/aiops-team-credentials-20261015.json').read_text())
    organizer = next(row for row in bundle['credentials'] if row.get('team_id') == 'ORGANIZER-TEST')
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get('/v1/final/tasks', headers={
        'Authorization': 'Bearer ' + organizer['token'],
        'X-Real-IP': '192.168.51.50',
    })
    if response.status_code != 200:
        raise RuntimeError(f'Final preview task list returned HTTP {response.status_code}')
    rows = response.json()['items']
    if len(rows) != 7 or not all(row.get('title') and row.get('description') for row in rows):
        raise RuntimeError('Final task titles or descriptions are missing')
    print(json.dumps({'status': 'FINAL_PUBLIC_COPY_VERIFIED',
                      'visible_preview_tasks': len(rows),
                      'tasks': [{'task_id': row['task_id'], 'title': row['title']}
                                for row in rows]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
