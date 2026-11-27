#!/usr/bin/env python3
"""Prepare the pinned quickstart, not a live mutation. Stdout contains identities only."""
import argparse
import copy
import importlib.util
import json
import os
import pathlib
import shutil
import re
import yaml


def replace_once(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        raise ValueError(f"{path}: upstream chart changed; review expected patch")
    path.write_text(text.replace(old, new, 1))


def prepare(source, out, renderer, probes, rabbitmq_plugins=None):
    if not isinstance(rabbitmq_plugins, list) or not all(isinstance(x, str) and re.fullmatch(r"[a-z][a-z0-9_]*", x) for x in rabbitmq_plugins) or "rabbitmq_prometheus" not in rabbitmq_plugins:
        raise ValueError("Supply the verified complete RabbitMQ enabled plugin list including rabbitmq_prometheus")
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
    # Preserve the deployed OrderedReady policy. Fresh bootstrap must be
    # tested; changing the immutable field on an existing StatefulSet is unsafe.
    if 'podManagementPolicy: OrderedReady' not in (nacos/'statefulset.yaml').read_text():
        raise ValueError('Review Nacos podManagementPolicy before deployment')
    replace_once(nacos/'statefulset.yaml', '            - name: MODE\n', '            - name: MANAGEMENT_ENDPOINTS_WEB_EXPOSURE_INCLUDE\n              value: "health,prometheus"\n            - name: MODE\n')
    rabbit = out/'rabbitmq/templates'
    plugin_file = 'enabled_plugins: |\n    [' + ','.join(sorted(set(rabbitmq_plugins))) + '].\n'
    (rabbit/'metrics-plugins.yaml').write_text('apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: {{ .Release.Name }}-metrics-plugins\n  namespace: {{ .Release.Namespace }}\ndata:\n  ' + plugin_file)
    replace_once(rabbit/'deployment.yaml', '        - name: rabbitmq\n', '        - name: rabbitmq\n          env:\n            - name: RABBITMQ_ENABLED_PLUGINS_FILE\n              value: /etc/rabbitmq/lab-plugins/enabled_plugins\n          volumeMounts:\n            - name: lab-metrics-plugins\n              mountPath: /etc/rabbitmq/lab-plugins\n              readOnly: true\n')
    replace_once(rabbit/'deployment.yaml', '      containers:\n', '      volumes:\n        - name: lab-metrics-plugins\n          configMap:\n            name: {{ .Release.Name }}-metrics-plugins\n      containers:\n')
    replace_once(rabbit/'deployment.yaml', '            - containerPort: {{ .Values.rabbitmq.serverPort }}', '            - containerPort: {{ .Values.rabbitmq.serverPort }}\n              name: amqp\n            - containerPort: 15692\n              name: prometheus')
    # Changing the plugin list must recreate the Pod (not just update the mounted file).
    replace_once(rabbit/'deployment.yaml', '      labels:\n        app: {{ .Release.Name }}', '      labels:\n        app: {{ .Release.Name }}\n      annotations:\n        checksum/lab-metrics-plugins: {{ include (print $.Template.BasePath "/metrics-plugins.yaml") . | sha256sum }}')
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
    print(json.dumps(prepare(a.source, a.out, a.renderer, probes, json.loads(os.environ["TT_RABBITMQ_ENABLED_PLUGINS"]))))
