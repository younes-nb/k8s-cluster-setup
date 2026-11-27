#!/usr/bin/env python3
"""Prepare the pinned quickstart, not a live mutation. Stdout contains identities only."""
import argparse
import copy
import importlib.util
import json
import os
import pathlib
import shutil
import yaml


def replace_once(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        raise ValueError(f"{path}: upstream chart changed; review expected patch")
    path.write_text(text.replace(old, new, 1))


def prepare(source, out, renderer, probes):
    spec = importlib.util.spec_from_file_location('renderer', renderer)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    quick = source / 'deployment/kubernetes-manifests/quickstart-k8s'
    out.mkdir(parents=True, exist_ok=True)
    deploys = list(yaml.safe_load_all((quick/'yamls/deploy.yaml.sample').read_text()))
    deploys = [d for d in deploys if d]
    services = [d for d in yaml.safe_load_all((quick/'yamls/svc.yaml').read_text()) if d]
    if len(deploys) != 46 or any(d['kind'] != 'Deployment' for d in deploys):
        raise ValueError('Pinned quickstart application inventory changed; review source')
    result = list(mod.render(copy.deepcopy(deploys), probes))
    def containers(docs):
        return {(d['metadata']['name'], c['name']): (c.get('resources'), c.get('env'), c.get('envFrom'), c.get('image')) for d in docs for c in d['spec']['template']['spec']['containers']}
    if containers(result) != containers(deploys):
        raise ValueError('Application resource, environment or image changed')
    secret_names = sorted({r['secretRef']['name'] for d in deploys for c in d['spec']['template']['spec']['containers'] for r in c.get('envFrom', []) if 'secretRef' in r})
    if not secret_names or any(not n.startswith('ts-') or not n.endswith('-mysql') for n in secret_names):
        raise ValueError('Unexpected DB secret references')
    (out/'deploy.yaml').write_text(yaml.safe_dump_all(result, sort_keys=False))
    (out/'svc.yaml').write_text(yaml.safe_dump_all(list(mod.render(services, {})), sort_keys=False))
    for name in ('nacos', 'rabbitmq'):
        chart = out/name
        shutil.copytree(quick/'charts'/name, chart, dirs_exist_ok=True)
    nacos = out/'nacos/templates'
    # Upstream uses the misspelled .passowrd key. Quote every interpolated secret field.
    f = nacos/'secrets.yaml'
    for old, new in [('{{.host}}','{{ .host | quote }}'), ('{{ .name }}','{{ .name | quote }}'), ('{{ .username }}','{{ .username | quote }}'), ('{{ .passowrd }}','{{ .passowrd | quote }}')]:
        replace_once(f, old, new)
    replace_once(nacos/'service.yaml', '  clusterIP: None', '  clusterIP: None\n  publishNotReadyAddresses: true')
    replace_once(nacos/'service.yaml', '      name: rpc\n      targetPort: 7848', '      name: rpc\n      targetPort: 7848\n    - {name: client-rpc, port: 9848, targetPort: 9848}\n    - {name: raft-rpc, port: 9849, targetPort: 9849}')
    # Parallel avoids OrderedReady init quorum deadlock on FRESH installs, but the
    # podManagementPolicy of an existing StatefulSet is immutable. Preserve
    # OrderedReady for existing installs (per-pod readiness is non-quorum, so no
    # deadlock); only switch to Parallel when explicitly requested for a fresh one.
    if os.environ.get('TT_NACOS_PARALLEL', '0') == '1':
        replace_once(nacos/'statefulset.yaml', 'podManagementPolicy: OrderedReady', 'podManagementPolicy: Parallel')
    for name, file, container in [('nacos','statefulset.yaml','k8snacos'), ('rabbitmq','deployment.yaml','rabbitmq')]:
        f = out/name/'templates'/file
        if name not in probes:
            raise ValueError(f'{name}: supply verified infrastructure readiness via TT_INFRA_PROBES')
        probe = probes[name]['readinessProbe']
        if not isinstance(probe, dict) or not any(k in probe for k in ('httpGet','exec','tcpSocket','grpc')):
            raise ValueError(f'{name}: invalid readinessProbe')
        fields = {'readinessProbe': probe}
        if 'startupProbe' in probes[name]:
            fields['startupProbe'] = probes[name]['startupProbe']
        block = '\n'.join('          '+line for line in yaml.safe_dump(fields, sort_keys=False).rstrip().splitlines())
        replace_once(f, f'        - name: {container}\n', f'        - name: {container}\n'+block+'\n')
        replace_once(f, '    spec:\n      ', '    spec:\n      nodeSelector:\n        lab.example.com/worker: "true"\n      ')
    return {'deployments': sorted(d['metadata']['name'] for d in result), 'db_secrets': secret_names}


if __name__ == '__main__':
    import os
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=pathlib.Path, required=True)
    ap.add_argument('--out', type=pathlib.Path, required=True)
    ap.add_argument('--renderer', type=pathlib.Path, required=True)
    a = ap.parse_args()
    probes = json.loads(os.environ['TT_PROBES'])
    probes.update(json.loads(os.environ['TT_INFRA_PROBES']))
    print(json.dumps(prepare(a.source, a.out, a.renderer, probes)))
