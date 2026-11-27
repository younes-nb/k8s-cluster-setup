"""Local PKI and static manifest tests; no Kubernetes or network access."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import yaml

HELPER = Path(__file__).resolve().parents[1]/'lab-setup/roles/cluster_addons/files/provision_metrics_certs.py'
spec = importlib.util.spec_from_file_location('metrics_certs', HELPER)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def manifest(root, component):
    pki = str(root/'pki')
    return {'apiVersion':'v1','kind':'Pod','metadata':{'name':component}, 'spec':{'containers':[{'name':component,'command':[component,'--authentication-kubeconfig=/etc/kubernetes/auth.conf','--authorization-kubeconfig=/etc/kubernetes/auth.conf'], 'volumeMounts':[{'name':'pki','mountPath':pki,'readOnly':True}]}], 'volumes':[{'name':'pki','hostPath':{'path':pki}}]}}


class TestMetricsCertificates(unittest.TestCase):
    def test_flag_reconciliation_is_idempotent(self):
        root = Path('/etc/kubernetes')
        doc = manifest(root,'kube-scheduler')
        cert,key = str(root/'pki/metrics/scheduler.crt'),str(root/'pki/metrics/scheduler.key')
        m.set_flags(doc,'kube-scheduler',cert,key)
        first = yaml.safe_dump(doc)
        m.set_flags(doc,'kube-scheduler',cert,key)
        self.assertEqual(first,yaml.safe_dump(doc))
    def test_conflicting_flags_rejected(self):
        root = Path('/etc/kubernetes'); doc=manifest(root,'kube-scheduler')
        doc['spec']['containers'][0]['command'].append('--tls-cert-file=/wrong.crt')
        with self.assertRaises(ValueError):
            m.set_flags(doc,'kube-scheduler',str(root/'pki/metrics/s.crt'),str(root/'pki/metrics/s.key'))
    def test_missing_delegated_auth_rejected(self):
        root = Path('/etc/kubernetes'); doc=manifest(root,'kube-scheduler')
        doc['spec']['containers'][0]['command'].pop()
        with self.assertRaises(ValueError):
            m.set_flags(doc,'kube-scheduler',str(root/'pki/metrics/s.crt'),str(root/'pki/metrics/s.key'))
    @unittest.skipUnless(shutil.which('openssl'),'requires OpenSSL')
    def test_fresh_then_adopt_without_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'manifests').mkdir()
            for component,*_ in m.COMPONENTS:
                f=root/'manifests'/(component+'.yaml')
                f.write_text(yaml.safe_dump(manifest(root,component)))
                shutil.copy2(f,f.with_suffix('.yaml.bak'))
            with patch.object(m,'wait_served'):
                self.assertTrue(m.provision(root))
                before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
                self.assertFalse(m.provision(root))
                self.assertEqual(before,{p:p.read_bytes() for p in root.rglob('*') if p.is_file()})
                self.assertEqual(len(list((root/'manifests').iterdir())),2)
                self.assertTrue((root/'metrics-signing/ca.key').is_file())
                # Legacy CA key is missing: adopt valid leaf certificates without replacing them.
                (root/'metrics-signing/ca.key').unlink()
                self.assertFalse(m.provision(root))
                # Partial state must fail instead of silently generating a different CA.
                (root/'pki/metrics/scheduler.key').unlink()
                with self.assertRaises(RuntimeError): m.provision(root)
    def test_rollback_on_failed_tls(self):
        # This path also exercises actual certificate generation, unless OpenSSL unavailable.
        if not shutil.which('openssl'): self.skipTest('requires OpenSSL')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'manifests').mkdir()
            for component,*_ in m.COMPONENTS:
                f=root/'manifests'/(component+'.yaml'); f.write_text(yaml.safe_dump(manifest(root,component)))
            f=root/'manifests/kube-scheduler.yaml'; before=f.read_bytes()
            with patch.object(m,'wait_served',side_effect=RuntimeError('mock handshake failure')):
                with self.assertRaises(RuntimeError): m.provision(root)
            self.assertEqual(f.read_bytes(),before)


if __name__=='__main__': unittest.main()
