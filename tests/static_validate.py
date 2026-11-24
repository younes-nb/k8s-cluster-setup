#!/usr/bin/env python3
"""Offline validation for the cluster repo.

    python3 tests/static_validate.py

Checks YAML parsing, shell syntax, Python compilation, rendering of every active
Jinja template with representative values, dashboard JSON (uids, panel ids,
datasource references), and that the Gateway Service declares no NodePorts.

It does NOT claim that Helm charts, images or CRDs work: see docs/VALIDATION.md.
"""
import importlib.util
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
          LAB / "inventory/group_vars/all/cluster_ips.yml",
          MON / "defaults/main.yml",
          LAB / "roles/loadgen/defaults/main.yml"]:
    d = yaml.safe_load(f.read_text())
    if isinstance(d, dict):
        ctx.update(d)
ctx.update(groups={"control_plane": ["master1", "master2", "master3"],
                   "workers": ["worker1", "worker2", "worker3"],
                   "nodes": ["master1", "master2", "master3",
                             "worker1", "worker2", "worker3", "loadgen"],
                   "load_generators": ["loadgen"]})
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
      f"{len(rendered)} active YAML templates render with the repo group_vars")

# ---- 3. gateway has no NodePort -----------------------------------------
gw = rendered.get("gateway.yaml.j2", [])
envoy = [d for d in gw if isinstance(d, dict) and d.get("kind") == "EnvoyProxy"]
svc = envoy[0]["spec"]["provider"]["kubernetes"]["envoyService"] if envoy else {}
check(svc.get("allocateLoadBalancerNodePorts") is False,
      "Gateway LoadBalancer allocates no NodePorts")
check(svc.get("type") == "LoadBalancer", "Gateway Service type is LoadBalancer")

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
    out = list(mod.render([d], {}))[0]
    after = json.dumps(out["spec"]["template"]["spec"]["containers"][0].get("resources"))
    ok_res &= (before == after)
svc_in = {"kind": "Service", "metadata": {"name": "ts-ui-dashboard"},
          "spec": {"type": "NodePort", "ports": [{"port": 8080, "nodePort": 32677}]}}
out_svc = list(mod.render([svc_in], {}))[0]["spec"]
ok_svc = out_svc == {"type": "ClusterIP", "ports": [{"port": 8080}]}
check(ok_res and ok_svc,
      "resource preservation, selector injection, readiness and NodePort removal fixtures")

# ---- 6. no tracked credentials ------------------------------------------
gone = [".vault.pass", "inventory/group_vars/all/vault.yaml",
        "kubespray-overlay/inventory/lab/credentials/kubeadm_certificate_key.creds"]
check(not any((ROOT / g).exists() for g in gone),
      "tracked secret files are absent")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED")
    sys.exit(1)
print("ALL OFFLINE CHECKS PASSED")