# AGENTS.md

Ansible + Kubespray repo that deploys an HA Kubernetes cluster (3 control-plane, 3 worker nodes, stacked etcd) on CloudLab hosts behind an HAProxy LB. No tests, no CI, no linters — "verification" means running an Ansible playbook.

## Entry point: `./run-cluster-setup.sh`

The end-to-end orchestrator (run from repo root, requires `ansible-playbook`, `python3`, `bash`). It runs local rendering steps first, then phases in this fixed order:

1. `tools/render-topology.yml` — renders `cluster-topology.yml` from `.env` (template `cluster-topology.yml.j2`).
2. `tools/generate-cluster-ips.yml` — resolves node FQDNs to IPv4 via `getent ahostsv4` and writes a generated `cluster_ips.yml` into BOTH `lab-setup/inventory/group_vars/all/` and `kubespray-overlay/inventory/lab/group_vars/all/`.
3. Phases: `preparing` → `haproxy` → `kubespray` → `postcluster` → `kubeconfig`.

Resume at any phase: `./run-cluster-setup.sh --start-from=kubespray` (or a bare step name). The script also contains an undocumented `kubectl` step (step 6, copies kubectl off `master1`) — it runs on a full run but is not in `usage()` and can't be used as a start point.

## Prerequisites not stored in the repo

- Copy `.env.example` → `.env` and fill in `CLOUDLAB_DOMAIN` and the `LB`/`MASTER1-3`/`WORKER1-3` hostname prefixes; node FQDNs are `<prefix>.<CLOUDLAB_DOMAIN>`. `.env`, `cluster-topology.yml`, and both `cluster_ips.yml` are generated + gitignored — never commit them.
- Node FQDNs must be DNS-resolvable from the host running the script (step 2 fails otherwise).
- `lab-setup/ansible.cfg` wires the vault password automatically to `lab-setup/.vault.pass` (a committed plaintext placeholder, works for the encrypted `vault.yaml`). `run-cluster-setup.sh` only adds `--vault-password-file` if `ANSIBLE_VAULT_PASSWORD_FILE` is set — set that to override.

## `kubespray/` is not source

`kubespray/` is gitignored: upstream Kubespray cloned at pinned `v2.30.0` (see `runner-init/group_vars/all.yml`) and synced with an overlay. All custom cluster config lives in `kubespray-overlay/inventory/lab/` (`inventory.ini` + `group_vars/...`), including generated `cluster_ips.yml`. The kubespray phase activates a venv at `kubespray/.venv` and runs `cluster.yml -b` with `-i ../kubespray-overlay/inventory/lab/inventory.ini`. Don't edit `kubespray/` directly — changes won't be committed; edit the overlay instead.

## Commands

- Full run: `./run-cluster-setup.sh`
- lab-setup plays (each has its own inventory; run with `-b` from `lab-setup/`): `ansible-playbook -i inventory/host.yaml playbook/{preparing,haproxy-lb,postcluster}.yaml -b`. Hosts: `preparing`→all nodes, `haproxy-lb`→`lb`, `postcluster`→`master1`.
- Kubespray: `source kubespray/.venv/bin/activate && ansible-playbook -i kubespray-overlay/inventory/lab/inventory.ini cluster.yml -b`
- `get-kubeconfig.sh` must be **sourced**, not executed (it exports `KUBECONFIG`, sets `alias k`, refuses to run standalone). Requires a local `kubectl`; fetches `/etc/kubernetes/admin.conf` from `master1` via ssh.
- Runner bootstrap (provisions the CloudLab runner that runs the pipeline): `cd runner-init && ansible-playbook -i inventory/runner.ini playbooks/bootstrap-runner.yml`. It needs the gitignored `runner-init/group_vars/cloudlab.local.yml` (defines `cloudlab_user`, `cloudlab_hosts`) — a fresh clone will fail without it.

## Gotchas

- `kubespray-overlay/inventory/lab/artifacts/admin.conf` and `.../credentials/kubeadm_certificate_key.creds` are tracked in git despite being listed in `.gitignore` (gitignore doesn't apply to already-tracked files) — treat them as live secrets.
- `postcluster.yaml` (tags: `deps`, `cert-issuer`, `monitoring`, `istio`, `online-boutique`) runs helm-based add-ons on `master1`; `preparing` is hardening/package setup across all nodes.
