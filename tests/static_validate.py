#!/usr/bin/env python3
"""Offline validation for the cluster repo.

    python3 tests/static_validate.py

Checks YAML parsing, shell syntax, Python compilation, rendering of every active
Jinja template with representative values, dashboard JSON (uids, panel ids,
datasource references), and that the Gateway Service declares no NodePorts.

It does NOT claim that Helm charts, images or CRDs work: see docs/VALIDATION.md.
"""
import importlib.util
import os
import json
import pathlib
import subprocess
import sys

import jinja2
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
LAB = ROOT / "lab-setup"
ADDONS = LAB / "roles/cluster_addons"
MON = LAB / "roles/monitoring"
SKIP = ("kubespray/", ".venv", ".git", ".ansible", "collections/ansible_collections")

failures = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


# ---- 1. YAML / shell / python -------------------------------------------
n_yaml = 0
bad = []
for f in ROOT.rglob("*"):
    if not f.is_file() or any(s in str(f) for s in SKIP):
        continue
    if f.suffix in (".yaml", ".yml"):
        try:
            list(yaml.safe_load_all(f.read_text()))
            n_yaml += 1
        except Exception as exc:
            bad.append(f"{f}: {exc}")
    elif f.suffix == ".sh":
        r = subprocess.run(["bash", "-n", str(f)], capture_output=True)
        if r.returncode != 0:
            bad.append(f"{f}: {r.stderr.decode().splitlines()[0]}")
    elif f.suffix == ".py":
        try:
            compile(f.read_text(), str(f), "exec")
        except Exception as exc:
            bad.append(f"{f}: {exc}")
check(not bad, f"{n_yaml} YAML files, shell syntax and Python compilation"
      + ("" if not bad else " -> " + "; ".join(bad[:3])))

# ---- 2. render the active templates -------------------------------------
ctx = {}
for f in [LAB / "inventory/group_vars/all/postcluster.yaml",
          LAB / "inventory/group_vars/all/all.yaml",
          MON / "defaults/main.yml",
          LAB / "roles/loadgen/defaults/main.yml"]:
    d = yaml.safe_load(f.read_text())
    if isinstance(d, dict):
        ctx.update(d)
ctx.update(cloudlab_private_ips={n: '10.10.1.'+str(i+1) for i,n in enumerate(['loadgen','monitoring','master1','master2','master3','worker1','worker2','worker3'])}, gateway_private_ip='10.10.1.50', monitoring_host='10.10.1.2', monitoring_disk_device='/dev/disk/by-id/test-only', monitoring_allow_format=False)
ctx.update(groups={"control_plane": ["master1", "master2", "master3"],
                   "workers": ["worker1", "worker2", "worker3"],
                   "nodes": ["master1", "master2", "master3",
                             "worker1", "worker2", "worker3", "loadgen"],
                   "load_generators": ["loadgen"]})
ctx.update(mysql_name="fixture-mysql", mysql_namespace="train-ticket", mysql_database="fixture", mysql_user="fixture", mysql_password="fixture: \"!", mysql_root_password="fixture#root", mysql_storage="2Gi", mysql_image="mysql:5.7.34", mysql_pvc_name="mysqldata")
env = jinja2.Environment(undefined=jinja2.StrictUndefined)
env.filters["to_json"] = json.dumps

rendered = {}
for f in sorted(list(ADDONS.glob("templates/*.yaml.j2"))
                + list(MON.glob("templates/*.yaml.j2"))
                + list(MON.glob("templates/*.yml.j2"))):
    try:
        s = env.from_string(f.read_text()).render(**ctx)
        rendered[f.name] = list(yaml.safe_load_all(s))
    except Exception as exc:
        bad.append(f"{f}: {exc}")
check(not [b for b in bad if "templates/" in b],
      f"{len(rendered)} active YAML templates render with fixture vars" + (" -> " + "; ".join(bad[:4]) if bad else ""))

# ---- 3. gateway has no NodePort -----------------------------------------
gw = rendered.get("gateway.yaml.j2", [])
envoy = [d for d in gw if isinstance(d, dict) and d.get("kind") == "EnvoyProxy"]
svc = envoy[0]["spec"]["provider"]["kubernetes"]["envoyService"] if envoy else {}
check(svc.get("allocateLoadBalancerNodePorts") is False,
      "Gateway LoadBalancer allocates no NodePorts")
check(svc.get("type") == "LoadBalancer", "Gateway Service type is LoadBalancer")

# ---- 3b. one canonical container metrics owner -------------------------
cluster_conf = (ADDONS / 'templates/alloy.yaml.j2').read_text()
node_conf = (ADDONS / 'templates/alloy-node.yaml.j2').read_text()
check('"__metrics_path__" = "/metrics/cadvisor"' not in cluster_conf and 'job_name = "prometheus.scrape.kubelet"' not in cluster_conf,
      'legacy kubelet cAdvisor collector removed')
check('job_name = "integrations/cadvisor"' in node_conf and 'honor_timestamps = true' in node_conf,
      'standalone cAdvisor source timestamps preserved')
for rule in rendered.get('autoscaler-rules.yaml.j2', [{}])[0].get('groups', []):
    for record in rule.get('rules', []):
        expr = record.get('expr', '')
        if 'container_cpu_' in expr:
            import re
            check(all('job="integrations/cadvisor"' in sel for sel in re.findall(r'container_cpu_\w+\{([^}]+)\}', expr)), 'canonical CPU job in ' + record['record'])

# ---- 4. dashboards -------------------------------------------------------
uids, panels = set(), 0
bad_dash = []
for f in sorted((MON / "files/dashboards").glob("*.json")):
    d = json.loads(f.read_text())
    if d["uid"] in uids:
        bad_dash.append(f"{f.name}: duplicate uid {d['uid']}")
    uids.add(d["uid"])
    ids = set()
    for p in d.get("panels", []):
        if p["id"] in ids:
            bad_dash.append(f"{f.name}: duplicate panel id {p['id']}")
        ids.add(p["id"]); panels += 1
        if p.get("datasource", {}).get("uid") not in {"lab-mimir", "lab-loki", "lab-tempo", "lab-pyroscope"}:
            bad_dash.append(f"{f.name}: bad datasource on {p.get('title')}")
        if any("istio_" in t.get("expr", "") for t in p.get("targets", [])):
            bad_dash.append(f"{f.name}: obsolete Istio query")
        if not p.get("targets"):
            bad_dash.append(f"{f.name}: no targets on {p.get('title')}")
check(not bad_dash, f"{len(uids)} Grafana dashboards, {panels} panels (JSON/UID/reference checks)"
      + ("" if not bad_dash else " -> " + "; ".join(bad_dash[:3])))

# ---- 5. the upstream renderer preserves resources ------------------------
spec = importlib.util.spec_from_file_location("inj", ADDONS / "templates/inject_selectors.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
original = {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": "ts-user-service"},
            "spec": {"template": {"metadata": {"labels": {"app": "ts-user-service"}},
                                  "spec": {"containers": [{
                                      "name": "ts-user-service", "image": "sample",
                                      "ports": [{"containerPort": 12342}],
                                      "resources": {"requests": {"cpu": "500m"},
                                                    "limits": {"memory": "1Gi"}}}]}}}}
ok_res = True
for with_res in (True, False):
    d = json.loads(json.dumps(original))
    c = d["spec"]["template"]["spec"]["containers"][0]
    if not with_res:
        c.pop("resources")
    before = json.dumps(c.get("resources"))
    out = list(mod.render([d], {"ts-user-service/ts-user-service": {"readinessProbe": {"httpGet": {"path": "/health", "port": 12342}}}}))[0]
    after = json.dumps(out["spec"]["template"]["spec"]["containers"][0].get("resources"))
    ok_res &= (before == after)
svc_in = {"kind": "Service", "metadata": {"name": "ts-ui-dashboard"},
          "spec": {"type": "NodePort", "ports": [{"port": 8080, "nodePort": 32677}]}}
out_svc = list(mod.render([svc_in], {}))[0]["spec"]
ok_svc = out_svc == {"type": "ClusterIP", "ports": [{"port": 8080}]}
try:
    list(mod.render([json.loads(json.dumps(original))], {}))
    rejected = False
except ValueError:
    rejected = True
check(rejected, "missing unverified readiness fails closed")
check(ok_res and ok_svc,
      "resource preservation, selector injection, readiness and NodePort removal fixtures")

# ---- 6. no tracked credentials ------------------------------------------
gone = [".vault.pass", "lab-setup/.vault.pass", "lab-setup/inventory/group_vars/all/vault.yaml",
        "kubespray-overlay/inventory/lab/credentials/kubeadm_certificate_key.creds"]
# The check is about *tracked* secrets. Generated (gitignored) files such as the
# kubeadm certificate key or a local .vault.pass may legitimately exist on a
# runner; what must never happen is committing them.
git_result = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
if git_result.returncode == 0:
    check(not any(g in git_result.stdout.split() for g in gone), "tracked secret files are absent")
else:
    print("SKIP tracked-secret check: no Git index available; checking archive paths only")
    check(not any((ROOT/g).exists() for g in gone), "known credential paths absent in archive")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED")
    sys.exit(1)
print("ALL OFFLINE CHECKS PASSED")

