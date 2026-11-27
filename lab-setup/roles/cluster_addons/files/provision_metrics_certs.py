#!/usr/bin/env python3
"""One master at a time. No network SSH, no CA rotation, no keys off the host.
Existing legacy certificates are adopted only when their current trust verifies.
Run via Ansible, not on the load generator. Requires openssl and PyYAML.
"""
import argparse
import datetime
import os
from pathlib import Path
import shutil
import ssl
import socket
import subprocess
import tempfile
import time
import yaml

COMPONENTS = [('kube-scheduler', 'scheduler', 'scheduler', 10259),
              ('kube-controller-manager', 'controller-manager', 'controller_manager', 10257)]


def run(*args):
    return subprocess.check_output(args, stderr=subprocess.STDOUT)


def atomic(path, data, mode):
    # The staging directory is outside the kubelet-watched manifests directory.
    with tempfile.NamedTemporaryFile(dir=path.parent.parent if path.parent.name == 'manifests' else path.parent, delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def backup(path, directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    out = directory / (path.name + '.' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    shutil.copy2(path, out)
    return out


def set_flags(document, component, cert_path, key_path):
    containers = document['spec']['containers']
    matching = [c for c in containers if c['name'] == component]
    if len(matching) != 1:
        raise ValueError(f'{component}: expected exactly one container')
    c = matching[0]
    cmd = c.get('command', [])
    if not cmd or Path(cmd[0]).name != component or c.get('args'):
        raise ValueError(f'{component}: unexpected command/args layout; review manually')
    # Require existing delegated authentication; do not invent credentials or weaken auth.
    for flag in ('authentication-kubeconfig', 'authorization-kubeconfig'):
        if not any(x.startswith('--'+flag+'=') and x.split('=',1)[1] for x in cmd):
            raise ValueError(f'{component}: missing --{flag}; configure delegated auth before provisioning')
    for flag, val in [('tls-cert-file', cert_path), ('tls-private-key-file', key_path)]:
        prefix = '--'+flag+'='
        existing = [x for x in cmd if x.startswith(prefix) or x == '--'+flag]
        if existing and existing != [prefix+val]:
            raise ValueError(f'{component}: conflicting {flag}; review existing cert configuration')
        if not existing:
            cmd.append(prefix+val)
    # Current helper provisions root-owned keys only. Non-root control-plane
    # layouts need explicit group ownership before changing anything.
    user = c.get('securityContext', {}).get('runAsUser', document['spec'].get('securityContext', {}).get('runAsUser', 0))
    if user != 0:
        raise ValueError(f'{component}: non-root static Pod; review key ownership')
    # The certificate/key must be reachable through an existing read-only host mount.
    volumes = {v['name']: v.get('hostPath', {}).get('path') for v in document['spec'].get('volumes', [])}
    for path in (cert_path, key_path):
        if not any(m.get('readOnly', False) and volumes.get(m['name']) == m.get('mountPath') and
                   (path == m['mountPath'] or path.startswith(m['mountPath'].rstrip('/')+'/'))
                   for m in c.get('volumeMounts', [])):
            raise ValueError(f'{component}: missing read-only PKI host mount for {path}')
    return document


def verify(cert, key, ca):
    run('openssl', 'verify', '-purpose', 'sslserver', '-verify_ip', '127.0.0.1', '-CAfile', str(ca), str(cert))
    run('openssl', 'x509', '-checkend', '604800', '-noout', '-in', str(cert))
    if run('openssl', 'x509', '-pubkey', '-noout', '-in', str(cert)) != run('openssl', 'pkey', '-pubout', '-in', str(key)):
        raise ValueError(f'{cert}: key mismatch')


def wait_served(cert, ca, port, timeout=180):
    expected = ssl.PEM_cert_to_DER_cert(cert.read_text())
    ctx = ssl.create_default_context(cafile=str(ca))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=3) as s:
                with ctx.wrap_socket(s, server_hostname='127.0.0.1') as tls:
                    if tls.getpeercert(binary_form=True) == expected:
                        return
        except (OSError, ssl.SSLError):
            pass
        time.sleep(3)
    raise RuntimeError(f'port {port}: expected trusted serving certificate not observed')


def provision(root):
    manifests = root/'manifests'
    pki = root/'pki/metrics'
    trust = root/'metrics-ca'
    backups = root/'backup/metrics-manifests'
    changed = False
    # Quarantine known legacy backup names only; never delete them.
    for component, *_ in COMPONENTS:
        old = manifests/(component+'.yaml.bak')
        if old.exists():
            backup(old, backups)
            old.unlink()
            changed = True
    pki.mkdir(parents=True, exist_ok=True, mode=0o700)
    trust.mkdir(parents=True, exist_ok=True, mode=0o755)
    plans = []
    for component, stem, truststem, port in COMPONENTS:
        manifest = manifests/(component+'.yaml')
        original = manifest.read_bytes()
        doc = yaml.safe_load(original)
        before = yaml.safe_dump(doc)
        cert, key, ca = pki/(stem+'.crt'), pki/(stem+'.key'), trust/(truststem+'.crt')
        set_flags(doc, component, str(cert), str(key))
        plans.append((manifest, original, doc, before != yaml.safe_dump(doc), cert, key, ca, port))
    signing = root/'metrics-signing'
    ca_key, ca_cert = signing/'ca.key', signing/'ca.crt'
    # Fresh only: a local signing CA is retained on this master. Never replace
    # a legacy CA whose key was lost, or overwrite partial credentials.
    artifacts = [x for plan in plans for x in plan[4:7]]
    if not any(x.exists() for x in artifacts + [ca_key, ca_cert, pki/'ca.key', pki/'ca.crt']):
        signing.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=pki) as d:
            d = Path(d)
            run('openssl','req','-x509','-newkey','rsa:3072','-nodes','-days','3650','-subj','/CN=lab-local-metrics-ca','-addext','basicConstraints=critical,CA:TRUE','-addext','keyUsage=critical,keyCertSign,cRLSign','-keyout',str(d/'ca.key'),'-out',str(d/'ca.crt'))
            for _,_,_,_,cert,key,ca,_ in plans:
                name = cert.stem
                ext = d/(name+'.ext')
                ext.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1\n')
                run('openssl','req','-new','-newkey','rsa:2048','-nodes','-subj','/CN='+name,'-keyout',str(d/key.name),'-out',str(d/(name+'.csr')))
                run('openssl','x509','-req','-in',str(d/(name+'.csr')),'-CA',str(d/'ca.crt'),'-CAkey',str(d/'ca.key'),'-CAcreateserial','-days','365','-extfile',str(ext),'-out',str(d/cert.name))
                verify(d/cert.name,d/key.name,d/'ca.crt')
            atomic(ca_key,(d/'ca.key').read_bytes(),0o600)
            atomic(ca_cert,(d/'ca.crt').read_bytes(),0o644)
            for _,_,_,_,cert,key,ca,_ in plans:
                atomic(key,(d/key.name).read_bytes(),0o600)
                atomic(cert,(d/cert.name).read_bytes(),0o644)
                atomic(ca,ca_cert.read_bytes(),0o644)
        changed = True
    for _,_,_,_,cert,key,ca,_ in plans:
        if not all(x.is_file() for x in (cert,key,ca)):
            raise RuntimeError('partial metrics PKI; inspect and recover explicitly, never rotate automatically')
        verify(cert,key,ca)
        if key.stat().st_mode & 0o077:
            os.chmod(key,0o600)
            changed = True
    for manifest, original, doc, needs_change, cert, key, ca, port in plans:
        if needs_change:
            backup(manifest,backups)
            atomic(manifest,yaml.safe_dump(doc,sort_keys=False).encode(),0o600)
            changed = True
        try:
            wait_served(cert,ca,port)
        except Exception:
            if needs_change:
                atomic(manifest,original,0o600)
            raise
    return changed


if __name__ == '__main__':
    os.umask(0o077)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, default=Path('/etc/kubernetes'))
    args = ap.parse_args()
    print('CHANGED' if provision(args.root) else 'UNCHANGED')
