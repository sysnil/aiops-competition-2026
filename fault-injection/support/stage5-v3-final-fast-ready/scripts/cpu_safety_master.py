#!/usr/bin/env python3
"""Run the existing CPU Safety v2 sampler with Master-local Calico PSI reads."""

import importlib.util
import json
from pathlib import Path
import subprocess


SOURCE = Path('/root/stage5-v3-clean-gate-v2/scripts/cpu_safety_sampler.py')
KUBECTL = ['/usr/bin/kubectl', '--kubeconfig=/etc/kubernetes/admin.conf',
           '--context=kubernetes-admin@kubernetes']


def main():
    spec = importlib.util.spec_from_file_location('cpu_safety_sampler', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def psi_sample(node):
        if node == 'k8s-master':
            text = Path('/proc/pressure/cpu').read_text()
        else:
            pods = json.loads(subprocess.check_output(KUBECTL + ['-n', 'calico-system',
                'get', 'pods', '-o', 'json'], text=True, timeout=20))['items']
            names = [row['metadata']['name'] for row in pods
                     if row['metadata']['name'].startswith('calico-node-') and
                     row['spec']['nodeName'] == node and
                     row['status']['phase'] == 'Running']
            if len(names) != 1:
                raise RuntimeError('Calico PSI source ambiguous: ' + node)
            text = subprocess.check_output(KUBECTL + ['-n', 'calico-system', 'exec',
                names[0], '-c', 'calico-node', '--', 'cat', '/proc/pressure/cpu'],
                text=True, timeout=20)
        match = module.PSI.search(text)
        if not match:
            raise RuntimeError('Node PSI absent: ' + node)
        return float(match.group(1))

    module.psi_sample = psi_sample
    module.main()


if __name__ == '__main__': main()
