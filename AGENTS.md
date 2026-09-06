# AGENTS.md — k8s-cluster-setup

## Quickstart

From repo root, run all steps:

```
./run-cluster-setup.sh
```

Or start at a specific step:

```
./run-cluster-setup.sh <step>
# steps: preparing | haproxy | kubespray | postcluster | kubeconfig
```

## Directory layout

- `lab-setup/` — Ansible playbooks/roles for node prep, HAProxy LB, post-cluster addons
- `kubespray-overlay/` — Kubespray inventory and group vars (the actual kubespray dir is at `lab-setup/../kubespray`, but the overlay vars are applied on top)
- `runner-init/` — Runner host bootstrap (SSH keys, etc.)
- `tools/` — Helper scripts: `render-topology.yml`, `generate-cluster-ips.yml`
- `get-kubeconfig.sh` — Fetch kubeconfig from a master node
- `.env` — Must be populated (copy from `.env.example`)

## Environment

- Copy `.env.example` to `.env` and set `CLOUDLAB_DOMAIN=emulab.net` (or your domain).
- The `run-cluster-setup.sh` script **must be run from the repo root** (it checks for `lab-setup/` and `kubespray/`).
- `python3`, `ansible-playbook`, and `bash` must be available on PATH.
- A Python venv is created at `kubespray/.venv` on first `kubespray` run; it installs deps from `kubespray/requirements.txt`.

## Workflow steps (in order)

1. **Rendering** — `tools/render-topology.yml` parses `.env` → `cluster-topology.yml` via template `cluster-topology.yml.j2`
2. **IP resolution** — `tools/generate-cluster-ips.yml` resolves CloudLab hostnames to IPs, writes `lab-setup/inventory/group_vars/all/cluster_ips.yml` and `kubespray-overlay/inventory/lab/group_vars/all/cluster_ips.yml`
3. **Preparing** — `lab-setup/playbook/preparing.yaml` runs role `preparing_server` on host `nodes`
4. **HAProxy LB** — `lab-setup/playbook/haproxy-lb.yaml` runs role `haproxy_lb` on host `lb`
5. **Kubespray** — `run-cluster-setup.sh` creates a venv at `kubespray/.venv`, installs deps from `kubespray/requirements.txt`, then runs:
   ```
   ansible-playbook -i ../kubespray-overlay/inventory/lab/inventory.ini cluster.yml -b
   ```
   from within `kubespray/`
6. **Post-cluster** — `lab-setup/playbook/postcluster.yaml` runs on `master1`: roles `k8s_ansible_deps` + `cluster_addons` (cert-issuer, monitoring, istio, online-boutique)
7. **Kubeconfig** — `source ./get-kubeconfig.sh master1 admin.conf` fetches kubeconfig and sets `KUBECONFIG`
8. **kubectl copy** — Copies kubectl from master1 to `~/.local/bin/kubectl`

## Critical gotchas

- **`.env` is the source of truth** for all hostnames/IPs. If it’s missing or incomplete, `cluster-topology.yml` will be wrong, and everything downstream fails.
- **Run from repo root** — the script aborts if `lab-setup/` or `kubespray/` is missing.
- **Kubespray venv** — the first `kubespray` run creates `kubespray/.venv`; subsequent runs reuse it. Do not delete it mid-flow.
- **Collections** — if `lab-setup/collections/requirements.yml` exists, collections are installed into `./.ansible/collections` via `ansible-galaxy install -r`.
- **Python deps** — `lab-setup/requirements.txt` (kubernetes, PyYAML, jsonpatch) are installed with `python3 -m pip install --user -r lab-setup/requirements.txt`.
- **`generate-cluster-ips.yml`** must run successfully before the Ansible playbooks, or the IP vars in the inventory will be missing/empty.
- **`get-kubeconfig.sh` must be sourced**, not executed: `source ./get-kubeconfig.sh master1 admin.conf`
- **Postcluster only runs on master1** — it targets `master1` specifically.
- The script uses `should_run` to skip steps; if you `--start-from=kubespray`, steps before kubespray are skipped entirely.