#!/usr/bin/env python3
"""Read-only identity preflight for the 17 cloned participant VMs."""

from __future__ import annotations

import base64
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

import pexpect


IPS = [f"192.168.51.{octet}" for octet in range(52, 69)]
TRUSTED_TEMPLATE_FINGERPRINT = "SHA256:9sGei5L7mWkB+hzqIrabIhWJJqpcDNrJYuqCW4dKGy0"
REMOTE_PRECHECK = r'''
import json, pathlib, socket, subprocess
ip = json.loads(subprocess.check_output(['ip', '-j', '-4', 'addr', 'show'], text=True))
links = json.loads(subprocess.check_output(['ip', '-j', 'link', 'show'], text=True))
addresses = {a['local']: nic['ifname'] for nic in ip for a in nic.get('addr_info', []) if a.get('family') == 'inet'}
macs = {nic['ifname']: nic.get('address') for nic in links}
unit = subprocess.run(['systemctl', 'cat', 'aiops-agent-web.service'], capture_output=True, text=True)
authorized = pathlib.Path('/root/.ssh/authorized_keys')
dbus = pathlib.Path('/var/lib/dbus/machine-id')
machine_id = pathlib.Path('/etc/machine-id').read_text().strip()
print(json.dumps({'hostname': socket.gethostname(), 'addresses': addresses,
                  'macs': macs, 'machine_id': machine_id,
                  'token_present': pathlib.Path('/etc/aiops-competition/team.env').exists(),
                  'web_unit_ok': unit.returncode == 0 and '/etc/aiops-competition/team.env' in unit.stdout,
                  'authorized_keys_present': authorized.exists(),
                  'authorized_keys_empty': not authorized.exists() or not authorized.read_text().strip(),
                  'backup_absent': not pathlib.Path('/root/aiops-team-provision-backup-20261020').exists(),
                  'dbus_id_consistent': not dbus.exists() or dbus.is_symlink() or dbus.read_text().strip() == machine_id,
                  'ssh_host_keys_present': all(pathlib.Path('/etc/ssh/ssh_host_' + kind + '_key').is_file()
                                               for kind in ('ed25519', 'ecdsa', 'rsa'))}))
'''

REMOTE_CONFIGURE = r'''
import base64, hashlib, json, os, pathlib, secrets, shutil, subprocess, sys, tempfile
request = json.loads(base64.b64decode(PAYLOAD).decode())
ip = json.loads(subprocess.check_output(['ip', '-j', '-4', 'addr', 'show'], text=True))
links = json.loads(subprocess.check_output(['ip', '-j', 'link', 'show'], text=True))
interfaces = {a['local']: nic['ifname'] for nic in ip for a in nic.get('addr_info', []) if a.get('family') == 'inet'}
macs = {nic['ifname']: nic.get('address') for nic in links}
if request['ip'] not in interfaces or macs[interfaces[request['ip']]] != request['mac']:
    raise SystemExit('GUEST_IDENTITY_CHANGED')
machine = pathlib.Path('/etc/machine-id')
if machine.is_symlink() or machine.read_text().strip() != request['old_machine_id']:
    raise SystemExit('MACHINE_ID_CHANGED')
token = pathlib.Path('/etc/aiops-competition/team.env')
if token.exists():
    raise SystemExit('TOKEN_ALREADY_PRESENT')
ssh_root = pathlib.Path('/root/.ssh')
ssh_root.mkdir(mode=0o700, exist_ok=True)
authorized = ssh_root / 'authorized_keys'
if authorized.is_symlink() or (authorized.exists() and authorized.read_text().strip()):
    raise SystemExit('EXISTING_AUTHORIZED_KEYS_REQUIRE_REVIEW')
dbus = pathlib.Path('/var/lib/dbus/machine-id')
if dbus.exists() and not dbus.is_symlink() and dbus.read_text().strip() != request['old_machine_id']:
    raise SystemExit('DBUS_MACHINE_ID_DIFFERS')
backup = pathlib.Path('/root/aiops-team-provision-backup-20261020')
if backup.exists():
    raise SystemExit('BACKUP_ALREADY_EXISTS')
backup.mkdir(mode=0o700)
keys = [pathlib.Path('/etc/ssh/ssh_host_' + kind + '_key') for kind in ('ed25519', 'ecdsa', 'rsa')]
sources = [machine] + ([dbus] if dbus.exists() and not dbus.is_symlink() else [])
for key in keys:
    sources.extend([key, pathlib.Path(str(key) + '.pub')])
if authorized.exists():
    sources.append(authorized)
for path in sources:
    if path.is_symlink() or not path.is_file():
        raise SystemExit('IDENTITY_FILE_INVALID')
    shutil.copy2(path, backup / path.name)
changed = []
try:
    with tempfile.TemporaryDirectory(prefix='.team-ssh-', dir='/etc/ssh') as directory:
        directory = pathlib.Path(directory)
        for kind, bits in (('ed25519', None), ('ecdsa', '256'), ('rsa', '3072')):
            tempkey = directory / ('ssh_host_' + kind + '_key')
            cmd = ['ssh-keygen', '-q', '-t', kind, '-N', '', '-f', str(tempkey)]
            if bits:
                cmd += ['-b', bits]
            subprocess.run(cmd, check=True, capture_output=True)
            destination = pathlib.Path('/etc/ssh/ssh_host_' + kind + '_key')
            for suffix in ('', '.pub'):
                os.replace(str(tempkey) + suffix, str(destination) + suffix)
                changed.append(pathlib.Path(str(destination) + suffix))
    new_id = secrets.token_hex(16) + '\n'
    for path in [machine] + ([dbus] if dbus.exists() and not dbus.is_symlink() else []):
        temporary = path.with_name('.' + path.name + '.team-new')
        temporary.write_text(new_id)
        os.chmod(temporary, 0o444)
        os.replace(temporary, path)
        changed.append(path)
    public = request['admin_public_key'].strip()
    if not public.startswith('ssh-ed25519 '):
        raise RuntimeError('ADMIN_KEY_INVALID')
    authorized.write_text('from="192.168.51.51",no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty ' + public + '\n')
    os.chmod(authorized, 0o600)
    subprocess.run(['/usr/sbin/sshd', '-t'], check=True, capture_output=True)
    subprocess.run(['systemctl', 'reload', 'ssh'], check=True, capture_output=True)
except Exception:
    for path in changed:
        saved = backup / path.name
        if saved.exists():
            shutil.copy2(saved, path)
    if (backup / 'authorized_keys').exists():
        shutil.copy2(backup / 'authorized_keys', authorized)
    else:
        authorized.unlink(missing_ok=True)
    subprocess.run(['systemctl', 'reload', 'ssh'], check=False, capture_output=True)
    raise
new_public = pathlib.Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text().split()
print(json.dumps({'status': 'CONFIGURED', 'ip': request['ip'], 'new_host_key': new_public[1],
                  'machine_id_sha256': hashlib.sha256(new_id.encode()).hexdigest(),
                  'backup': str(backup)}))
'''


def scan_key(ip: str) -> tuple[str, str]:
    for _ in range(3):
        result = subprocess.run(['ssh-keyscan', '-T', '5', '-t', 'ed25519', ip],
                                capture_output=True, text=True, timeout=8)
        lines = [line for line in result.stdout.splitlines() if line.startswith(ip + ' ssh-ed25519 ')]
        if len(lines) == 1:
            parts = lines[0].split()
            digest = hashlib.sha256(base64.b64decode(parts[2])).digest()
            fingerprint = 'SHA256:' + base64.b64encode(digest).decode().rstrip('=')
            return lines[0], fingerprint
    raise RuntimeError(f'{ip}: no unique ED25519 host key')


def remote_check(ip: str, password: str, known_hosts: Path) -> dict:
    args = ['-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={known_hosts}',
            '-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no',
            '-o', 'NumberOfPasswordPrompts=1', '-o', 'ConnectTimeout=7', f'root@{ip}',
            'python3 -c ' + __import__('shlex').quote(REMOTE_PRECHECK)]
    child = pexpect.spawn('ssh', args, encoding='utf-8', timeout=15, echo=False)
    try:
        child.expect('password:')
        child.sendline(password)
        child.expect(pexpect.EOF)
        output = child.before
        child.close()
        if child.exitstatus != 0:
            raise RuntimeError(f'{ip}: authenticated SSH preflight failed: {output.strip()[-160:]}')
        result = json.loads(output.strip().splitlines()[-1])
        if ip not in result['addresses']:
            raise RuntimeError(f'{ip}: guest IP mismatch')
        result['ip'] = ip
        result['mac'] = result['macs'][result['addresses'][ip]]
        result.pop('macs')
        result.pop('addresses')
        return result
    finally:
        child.close()


def remote_configure(ip: str, password: str, known_hosts: Path, payload: dict) -> dict:
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    program = 'PAYLOAD=' + repr(encoded) + '\n' + REMOTE_CONFIGURE
    args = ['-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={known_hosts}',
            '-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no',
            '-o', 'NumberOfPasswordPrompts=1', '-o', 'ConnectTimeout=7', f'root@{ip}',
            'python3 -c ' + shlex.quote(program)]
    child = pexpect.spawn('ssh', args, encoding='utf-8', timeout=30, echo=False)
    try:
        child.expect('password:')
        child.sendline(password)
        child.expect(pexpect.EOF)
        output = child.before
        child.close()
        if child.exitstatus != 0:
            raise RuntimeError(f'{ip}: SSH identity configuration failed: {output.strip()[-180:]}')
        return json.loads(output.strip().splitlines()[-1])
    finally:
        child.close()


def configure_hosts(rows: list[dict], password: str, old_known_hosts: Path) -> list[dict]:
    private_key = Path('/root/.ssh/id_ed25519')
    public_key = Path('/root/.ssh/id_ed25519.pub')
    if private_key.exists() or public_key.exists():
        raise RuntimeError('51 management key path is already occupied')
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C',
                    'aiops-contest-51-team-delivery-20261020', '-f', str(private_key)], check=True)
    os.chmod(private_key, 0o600)
    admin_public_key = public_key.read_text().strip()
    known_hosts = Path('/root/.ssh/known_hosts')
    if any(subprocess.run(['ssh-keygen', '-F', row['ip'], '-f', str(known_hosts)],
                          capture_output=True).returncode == 0 for row in rows):
        raise RuntimeError('51 known_hosts has an existing team entry; review before appending')
    results = []
    for row in rows:
        ip = row['ip']
        payload = {'ip': ip, 'mac': row['mac'], 'old_machine_id': row['machine_id'],
                   'admin_public_key': admin_public_key}
        result = remote_configure(ip, password, old_known_hosts, payload)
        if result.get('status') != 'CONFIGURED' or result.get('ip') != ip:
            raise RuntimeError(f'{ip}: invalid configure response')
        expected = result['new_host_key']
        for _ in range(4):
            try:
                line, _ = scan_key(ip)
            except RuntimeError:
                continue
            if line.split()[2] == expected:
                break
        else:
            raise RuntimeError(f'{ip}: rotated SSH key was not observed')
        with known_hosts.open('a') as handle:
            handle.write(line + '\n')
        subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'PasswordAuthentication=no',
                        '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5',
                        f'root@{ip}', 'true'], check=True, capture_output=True, timeout=10)
        results.append({'ip': ip, 'status': 'SSH_KEY_VERIFIED',
                        'machine_id_sha256': result['machine_id_sha256']})
        print(json.dumps(results[-1]), flush=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--configure', action='store_true', help='rotate clone identities and configure 51 public-key access')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('run as root on 51')
    keys = [scan_key(ip) for ip in IPS]
    fingerprints = {fingerprint for _, fingerprint in keys}
    if fingerprints != {TRUSTED_TEMPLATE_FINGERPRINT}:
        raise SystemExit('host key changed from the independently recorded 52 template; stop')
    password = getpass.getpass('Clone root password (not logged): ')
    try:
        with tempfile.TemporaryDirectory(prefix='team-host-preflight-', dir='/root') as directory:
            known_hosts = Path(directory) / 'known_hosts'
            known_hosts.write_text('\n'.join(key for key, _ in keys) + '\n')
            known_hosts.chmod(0o600)
            rows = [remote_check(ip, password, known_hosts) for ip in IPS]
            macs = [row['mac'] for row in rows]
            if args.configure:
                if len(set(macs)) != 17 or not all(
                        row['web_unit_ok'] and not row['token_present']
                        and row['authorized_keys_empty'] and row['backup_absent']
                        and row['dbus_id_consistent'] and row['ssh_host_keys_present'] for row in rows):
                    raise RuntimeError('all 17 hosts must pass identity, Web, backup and token preflight')
                results = configure_hosts(rows, password, known_hosts)
                print(json.dumps({'status': 'SSH_SETUP_VERIFIED', 'hosts': results}, indent=2))
                return 0
    finally:
        password = ''
    macs = [row['mac'] for row in rows]
    ids = [row['machine_id'] for row in rows]
    result = {
        'status': 'PREFLIGHT_PASS' if len(set(macs)) == 17 and all(row['web_unit_ok'] for row in rows) else 'PREFLIGHT_BLOCKED',
        'hosts': [{key: value for key, value in row.items() if key != 'machine_id'} for row in rows],
        'unique_macs': len(set(macs)), 'unique_machine_ids': len(set(ids)),
        'unique_ssh_host_keys': len(fingerprints),
        'existing_token_files': sum(row['token_present'] for row in rows),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'PREFLIGHT_PASS' else 2


if __name__ == '__main__':
    sys.exit(main())
