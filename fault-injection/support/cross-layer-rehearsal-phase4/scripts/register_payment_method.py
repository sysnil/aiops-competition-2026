#!/usr/bin/env python3
"""Freeze Payment reconnect rules and source versions before business tests."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path('/root/cross-layer-rehearsal-phase4')
FILES = [ROOT / 'design/plan.yaml', ROOT / 'design/payment-connection-policy.yaml',
         ROOT / 'design/checkout-identity-plan.json',
         ROOT / 'runtime/payment-setup.json',
         ROOT / 'backups/deployment-checkout.json',
         ROOT / 'observations/checkout-main-go-3.0.0.txt',
         *[ROOT / 'scripts' / name for name in (
             'checkout_identity_rollout.py', 'payment_setup.py', 'payment_cleanup.py',
             'reconnect_frontend_clients.py', 'run_payment_window.py',
             'audit_payment_flow.py', 'capture_pipeline.py', 'run_load_pair.py')]]


def main():
    output = ROOT / 'design/payment-method-manifest.json'
    if output.exists():
        raise ValueError('Payment method already registered')
    setup = json.loads((ROOT / 'runtime/payment-setup.json').read_text())
    if setup['status'] != 'PAYMENT_CLIENT_SETUP_PASS':
        raise ValueError('Temporary four-Checkout setup failed')
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}
    result = {'phase': 'cross-layer-rehearsal-phase4',
              'component': 'payment_multiclient_business_coverage',
              'registered_at_utc': datetime.now(timezone.utc).isoformat(),
              'checkout_pods_ready_at_setup': 4,
              'frontend_replacements_planned': 2,
              'retries_based_on_backend_choice': 0,
              'payment_faults_injected': 0,
              'no_formal_case': True,
              'sha256': hashes}
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': 'PHASE4_PAYMENT_METHOD_REGISTERED',
                      'files': len(hashes),
                      'manifest_sha256': hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
