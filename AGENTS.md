# AGENTS.md — k8s-cluster-setup

Ansible-driven Kubespray deployment on CloudLab (1 LB + 3 masters + 3 workers).

## Order of operations

1. **Runner bootstrap first** (fresh runner host only): `runner-init/playbooks/bootstrap-runner.yml`.
   Clones this repo to `~/cluster-repo`, clones Kubespray **v2.30.0** next to it, installs
   `~/.ssh/config` + system SSH aliases (`lb`, `master1..3`, `worker1..3`).
   Needs per-host `runner-init/group_vars/cloudlab.local.yml` (gitignored FQDNs + `cloudlab_user`).
2. **Full deploy from repo root**: `./run-cluster-setup.sh`
   (`./run-cluster-setup.sh --start-from=<step> | <step>`; steps: `preparing haproxy kubespray postcluster kubeconfig`)

## Source of truth → generated files (do not edit generated)

- `.env` (gitignored, copy from `.env.example`, all 8 vars required) → `tools/render-topology.yml`
  → `cluster-topology.yml` (gitignored) → `tools/generate-cluster-ips.yml` resolves FQDNs via
  `getent ahostsv4` → `cluster_ips.yml` under both `lab-setup/.../all/` and `kubespray-overlay/.../all/`.
- Render + IP generation **always run** at script start, even with `--start-from` (only the
  `preparing|haproxy|kubespray|postcluster|kubeconfig` blocks are gated by `should_run`).
- `generate-cluster-ips.yml` asserts every host resolves to IPv4 — DNS failure aborts before anything runs.

## Gotchas

- **Run from repo root.** Script aborts without `lab-setup/` + `kubespray/`; `tools/*.yml` use
  relative `../` paths, so manual `ansible-playbook tools/...` also requires repo-root cwd.
- **`kubespray/` is not in git** — it is cloned by runner bootstrap (pinned `v2.30.0` in
  `runner-init/group_vars/all.yml`). If missing, bootstrap the runner; don't clone another version.
  First `kubespray` step creates `kubespray/.venv` from `kubespray/requirements.txt` and reuses it.
- **Inventories use bare SSH aliases** (`master1`, `lb`, … in `lab-setup/inventory/host.yaml` and
  `kubespray-overlay/.../inventory.ini`). Without bootstrap's ssh_config aliases, nothing connects.
- **Run lab-setup playbooks from `lab-setup/`** (`pushd` as the script does): `ansible.cfg` sets
  relative `inventory`, `roles_path`, and `vault_password_file=./.vault.pass` (committed).
  From another cwd, vault decryption and role lookup break. `ANSIBLE_VAULT_PASSWORD_FILE` env
  overrides the vault password file when set.
- **Kubespray step reads the overlay in place**: from inside `kubespray/`,
  `-i ../kubespray-overlay/inventory/lab/inventory.ini`. (Bootstrap also rsyncs the overlay into
  `kubespray/inventory/lab`, but the script uses the overlay path — edit the overlay, not the copy.)
  Overlay LB/API wiring consumes generated `lb_ip`/`master_ips` (`supplementary_addresses_in_ssl_keys`,
  `loadbalancer_apiserver`).
- **`get-kubeconfig.sh` must be sourced** (`source ./get-kubeconfig.sh master1 admin.conf`) — it exports
  `KUBECONFIG`. Sourcing it inside `run-cluster-setup.sh` does not persist to your shell; re-source manually.
- **`kubectl` copy runs before `kubeconfig`** (undocumented in `usage()`, so it can't be used with
  `--start-from`): the script `scp`s kubectl from `master1` to `~/.local/bin/kubectl` first, which
  satisfies `get-kubeconfig.sh`'s local-`kubectl` requirement even on fresh runners.
- **Postcluster targets `master1` only** (`lab-setup/playbook/postcluster.yaml`: `k8s_ansible_deps` +
  `cluster_addons`). Rerun one addon selectively with `--tags cert-issuer|monitoring|istio|online-boutique`
  (plus `deps` for the prereq role).
- Script exports `ANSIBLE_SSH_CONTROL_PATH_DIR=/tmp/ansible-cp`, installs collections to
  `./.ansible/collections`, and `pip install --user -r lab-setup/requirements.txt` on every run.
