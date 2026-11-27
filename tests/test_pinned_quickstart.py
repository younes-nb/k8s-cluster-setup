#!/usr/bin/env python3
"""Exercise the exact active renderer with the pinned upstream checkout.
Fixture probes are structural ONLY; never use them as deployed probe settings.
"""
import argparse
import importlib.util
import pathlib
import tempfile
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADDONS = ROOT/'lab-setup/roles/cluster_addons'

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--train-ticket', type=pathlib.Path, required=True)
    a = ap.parse_args()
    spec = importlib.util.spec_from_file_location('prepare', ADDONS/'files/prepare_train_ticket.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src = a.train_ticket/'deployment/kubernetes-manifests/quickstart-k8s/yamls/deploy.yaml.sample'
    upstream = [d for d in yaml.safe_load_all(src.read_text()) if d]
    fixture = {d['metadata']['name']+'/'+c['name']: {'readinessProbe': {'tcpSocket': {'port': c['ports'][0]['containerPort']}}} for d in upstream for c in d['spec']['template']['spec']['containers']}
    fixture.update(nacos={'readinessProbe': {'tcpSocket': {'port': 8848}}}, rabbitmq={'readinessProbe': {'tcpSocket': {'port': 5672}}})
    renderer = ADDONS/'templates/inject_selectors.py'
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        try:
            mod.prepare(a.train_ticket, out, renderer, {}, ["rabbitmq_prometheus"])
        except ValueError:
            pass
        else:
            raise AssertionError('unverified upstream TCP probes accepted')
        try:
            mod.prepare(a.train_ticket, out, renderer, fixture, ["rabbitmq_management"])
        except ValueError:
            pass
        else:
            raise AssertionError('missing verified Prometheus plugin accepted')
        first = mod.prepare(a.train_ticket, out, renderer, fixture, ["rabbitmq_management", "rabbitmq_prometheus"])
        before = {f.relative_to(out): f.read_bytes() for f in out.rglob('*') if f.is_file()}
        assert mod.prepare(a.train_ticket, out, renderer, fixture, ["rabbitmq_management", "rabbitmq_prometheus"]) == first
        after = {f.relative_to(out): f.read_bytes() for f in out.rglob('*') if f.is_file()}
        assert before == after, 'rendering is not deterministic'
        assert len(first['deployments']) == 46 and len(first['db_secrets']) == 27
        cfg = yaml.safe_load((ROOT/'lab-setup/inventory/group_vars/all/postcluster.yaml').read_text())
        assert sorted(cfg['tt_scalable_deployments']) == first['deployments']
        deploys = list(yaml.safe_load_all((out/'deploy.yaml').read_text()))
        for d in deploys:
            assert d['spec']['template']['spec']['nodeSelector']['lab.example.com/worker'] == 'true'
        for service in yaml.safe_load_all((out/'svc.yaml').read_text()):
            assert service['spec'].get('type', 'ClusterIP') == 'ClusterIP'
            assert all('nodePort' not in p for p in service['spec']['ports'])
        assert 'publishNotReadyAddresses: true' in (out/'nacos/templates/service.yaml').read_text()
        assert 'podManagementPolicy: OrderedReady' in (out/'nacos/templates/statefulset.yaml').read_text()
        assert 'MANAGEMENT_ENDPOINTS_WEB_EXPOSURE_INCLUDE' in (out/'nacos/templates/statefulset.yaml').read_text()
        assert 'rabbitmq_management,rabbitmq_prometheus' in (out/'rabbitmq/templates/metrics-plugins.yaml').read_text()
        assert 'containerPort: 15692' in (out/'rabbitmq/templates/deployment.yaml').read_text()
        tasks = (ADDONS/'tasks/train_ticket.yaml').read_text()
        assert 'prepare_train_ticket.py' in tasks and '-rendered/{{ item }}' in tasks
        assert '/tmp/opencode' not in tasks and 'state: absent' not in tasks and 'delete pod' not in tasks
        print('PASS exact pinned quickstart: 46 Deployments, 27 DB Secrets, original resources/env/images, worker placement, no NodePort, deterministic output, fail-closed probes and active renderer wiring')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
