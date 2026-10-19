#!/usr/bin/env bash
# Orchestrates full cluster setup end-to-end
# Steps:
#  1) lab-setup/preparing.yaml          (name: preparing)
#  2) lab-setup/haproxy-lb.yaml         (name: haproxy)
#  3) kubespray cluster.yml (with venv) (name: kubespray)
#  4) lab-setup/postcluster.yaml        (name: postcluster)
#  5) copy kubectl from master1 to runner (name: kubectl)
#  6) get kubeconfig into ./admin.conf    (name: kubeconfig)

set -Eeuo pipefail

# ---------- config ----------
LAB_DIR="lab-setup"
KUBESPRAY_DIR="kubespray"

KS_DIR_ABS="$(cd "$KUBESPRAY_DIR" && pwd)"
KS_VENV="${KS_DIR_ABS}/.venv"
KS_REQUIREMENTS="${KS_DIR_ABS}/requirements.txt"
# kubespray/requirements.txt pins ansible==12.3.0, which needs Python 3.12
KS_PYTHON="${KS_PYTHON:-python3.12}"

GET_KUBECONFIG="./get-kubeconfig.sh"
KUBECONFIG_DEST="admin.conf"
KUBECONFIG_HOST="master1"

ANSIBLE_VAULT_FLAG=${ANSIBLE_VAULT_PASSWORD_FILE:+--vault-password-file "$ANSIBLE_VAULT_PASSWORD_FILE"}

# ---------- helpers ----------
ok()   { echo -e "\033[1;32m✔ $*\033[0m"; }
info() { echo -e "\033[1;34mℹ $*\033[0m"; }
err()  { echo -e "\033[1;31m✘ $*\033[0m"; }

# True if <interpreter> is exactly Python 3.12 (the pinned kubespray
# interpreter; ansible 12.x needs >= 3.11, this repo pins 3.12).
ks_python_ok() {
  "$1" -c 'import sys; exit(0 if sys.version_info[:2] == (3, 12) else 1)' >/dev/null 2>&1
}

# True if the kubespray venv exists and was built with Python 3.12.
ks_venv_ok() {
  [ -x "${KS_VENV}/bin/python" ] && ks_python_ok "${KS_VENV}/bin/python"
}

step_banner() {
  echo -e "\n\033[1;36m==> $*\033[0m"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { err "Missing required command: $1"; exit 127; }
}

# Copies kubectl from master1 and puts it on PATH for this shell.
# NOTE: never `source ~/.bashrc` here. Non-interactive shells return
# early from the default Ubuntu .bashrc, so the PATH export would never
# apply — and any failing command in .bashrc would abort this script
# under `set -e`.
install_kubectl() {
  mkdir -p "$HOME/.local/bin"
  scp "${KUBECONFIG_HOST}:/usr/local/bin/kubectl" "$HOME/.local/bin/kubectl"
  chmod +x "$HOME/.local/bin/kubectl"
  touch "$HOME/.bashrc"
  grep -q 'export PATH=$HOME/.local/bin:$PATH' "$HOME/.bashrc" \
    || echo 'export PATH=$HOME/.local/bin:$PATH' >> "$HOME/.bashrc"
  export PATH="$HOME/.local/bin:$PATH"
  kubectl version --client
}

usage() {
  cat <<EOF
Usage: $0 [--start-from=STEP] | [STEP]

Steps (in order):
  preparing   - run lab-setup/preparing.yaml
  haproxy     - run lab-setup/haproxy-lb.yaml
  kubespray   - deploy cluster with kubespray (venv + cluster.yml)
  postcluster - run lab-setup/postcluster.yaml
  kubectl     - copy kubectl from master1 to ~/.local/bin
  kubeconfig  - fetch kubeconfig via get-kubeconfig.sh

Examples:
  $0                          # run all steps
  $0 --start-from=kubespray   # start at kubespray and continue
  $0 postcluster              # start at postcluster and continue
EOF
}

# ---------- arg parsing ----------
START_FROM=""
if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --start-from=*) START_FROM="${1#*=}" ;;
    preparing|haproxy|kubespray|postcluster|kubectl|kubeconfig) START_FROM="$1" ;;
    *) err "Unknown argument: $1"; usage; exit 2 ;;
  esac
fi

START_FROM="$(echo -n "$START_FROM" | tr '[:upper:]' '[:lower:]')"

SKIP_UNTIL="${START_FROM:-}"
should_run() {
  local name="$1"
  if [[ -z "$SKIP_UNTIL" ]]; then
    return 0
  fi
  if [[ "$name" == "$SKIP_UNTIL" ]]; then
    SKIP_UNTIL=""
    return 0
  fi
  return 1
}

# ---------- preflight ----------
need_cmd ansible-playbook
need_cmd python3
need_cmd bash

[[ -d "$LAB_DIR" && -d "$KUBESPRAY_DIR" ]] || { err "Run from repo root. Missing $LAB_DIR/ or $KUBESPRAY_DIR/"; exit 1; }
[[ -f "$GET_KUBECONFIG" ]] || { err "Missing $GET_KUBECONFIG in repo root"; exit 1; }

# Generated files must be writable by the invoking user. Parts of this
# checkout were created as root, which otherwise surfaces later as a
# cryptic "Destination ... not writable" from the generate-cluster-ips play.
for _f in cluster-topology.yml \
  "${LAB_DIR}/inventory/group_vars/all/cluster_ips.yml" \
  "kubespray-overlay/inventory/lab/group_vars/all/cluster_ips.yml" \
  "${KUBECONFIG_DEST}"; do
  _d="$(dirname "$_f")"
  if [[ -e "$_f" && ! -w "$_f" ]] || [[ ! -e "$_f" && ! -w "$_d" ]]; then
    err "Cannot write $_f. Fix ownership, e.g.: sudo chown -R $(id -un):$(id -gn) lab-setup kubespray-overlay"
    exit 1
  fi
done
unset _f _d

export ANSIBLE_SSH_CONTROL_PATH_DIR=/tmp/ansible-cp
mkdir -p /tmp/ansible-cp
chmod 700 /tmp/ansible-cp

step_banner "Rendering cluster-topology.yml from .env"
ansible-playbook -i localhost, -c local tools/render-topology.yml
ok "Rendered cluster-topology.yml"

step_banner "Generating dynamic cluster IP variables"
ansible-playbook -i localhost, -c local tools/generate-cluster-ips.yml
ok "Generated dynamic IP variables"

# ---------- collections / deps (lab-setup) ----------
if [[ -f "${LAB_DIR}/collections/requirements.yml" ]]; then
  step_banner "Ensuring Ansible collections are installed (lab-setup)"
  export ANSIBLE_COLLECTIONS_PATHS="$(pwd)/.ansible/collections:${ANSIBLE_COLLECTIONS_PATHS:-$HOME/.ansible/collections:/usr/share/ansible/collections}"
  mkdir -p "$(pwd)/.ansible/collections"

  info "ansible-galaxy collection install -r ${LAB_DIR}/collections/requirements.yml"
  ansible-galaxy collection install -r "${LAB_DIR}/collections/requirements.yml" -p "$(pwd)/.ansible/collections" >/dev/null
  ok "Collections installed/updated"
else
  info "No ${LAB_DIR}/collections/requirements.yml found; skipping collection install"
fi

if [[ -f "${LAB_DIR}/requirements.txt" ]]; then
  step_banner "Ensuring Python deps are installed (lab-setup)"
  python3 -m pip install --user -r "${LAB_DIR}/requirements.txt" >/dev/null
  ok "Python deps installed"
fi

# ---------- 1) Preparing play ----------
if should_run preparing; then
  step_banner "Running preparing playbook (lab-setup)"
  pushd "$LAB_DIR" >/dev/null
  info "ansible-playbook -i inventory/host.yaml playbook/preparing.yaml -b"
  ansible-playbook -i inventory/host.yaml playbook/preparing.yaml -b ${ANSIBLE_VAULT_FLAG:-}
  ok "Preparing playbook finished"
  popd >/dev/null
else
  info "Skipping step: preparing"
fi

# ---------- 2) HAProxy LB play ----------
if should_run haproxy; then
  step_banner "Running HAProxy LB playbook (lab-setup)"
  pushd "$LAB_DIR" >/dev/null
  info "ansible-playbook -i inventory/host.yaml playbook/haproxy-lb.yaml -b"
  ansible-playbook -i inventory/host.yaml playbook/haproxy-lb.yaml -b ${ANSIBLE_VAULT_FLAG:-}
  ok "HAProxy LB playbook finished"
  popd >/dev/null
else
  info "Skipping step: haproxy"
fi

# ---------- 3) Kubespray cluster (with venv) ----------
if should_run kubespray; then
  step_banner "Deploying cluster with Kubespray"
  pushd "$KS_DIR_ABS" >/dev/null

  # The kubespray venv must be built with the pinned Python 3.12 (ansible
  # 12.x needs >= 3.11). A stale venv (e.g. created with the system Python
  # 3.10, whose pip cannot resolve ansible==12.3.0) is rebuilt in place.
  if command -v "$KS_PYTHON" >/dev/null 2>&1 && ks_python_ok "$KS_PYTHON"; then
    KS_PY="$KS_PYTHON"
  else
    err "Kubespray requires Python 3.12, but '$KS_PYTHON' was not found (or is another version)."
    err "Ubuntu 22.04: sudo add-apt-repository -y ppa:deadsnakes/ppa \\"
    err "        && sudo apt-get update && sudo apt-get install -y python3.12 python3.12-venv"
    err "Ubuntu 24.04+: sudo apt-get install -y python3.12 python3.12-venv"
    exit 1
  fi

  if ! ks_venv_ok; then
    info "Creating Kubespray virtualenv at $KS_VENV (with $KS_PY)"
    if ! "$KS_PY" -m venv --clear "$KS_VENV"; then
      err "venv creation failed with $KS_PY. If the error mentions ensurepip,"
      err "install the matching venv package first, e.g. apt install ${KS_PY}-venv."
      exit 1
    fi
  fi

  source "$KS_VENV/bin/activate"
  info "Ensuring Kubespray requirements are installed"
  pip install --upgrade pip >/dev/null
  pip install -r "$KS_REQUIREMENTS" >/dev/null

  info "ansible-playbook -i ../kubespray-overlay/inventory/lab/inventory.ini cluster.yml -b"
  ansible-playbook -i ../kubespray-overlay/inventory/lab/inventory.ini cluster.yml -b ${ANSIBLE_VAULT_FLAG:-}
  deactivate
  ok "Kubespray cluster deployment finished"
  popd >/dev/null
else
  info "Skipping step: kubespray"
fi

# ---------- 4) Postcluster play ----------
if should_run postcluster; then
  step_banner "Running postcluster playbook (lab-setup)"
  pushd "$LAB_DIR" >/dev/null
  info "ansible-playbook -i inventory/host.yaml playbook/postcluster.yaml -b"
  ansible-playbook -i inventory/host.yaml playbook/postcluster.yaml -b ${ANSIBLE_VAULT_FLAG:-}
  ok "Postcluster playbook finished"
  popd >/dev/null
else
  info "Skipping step: postcluster"
fi

# ---------- 5) Copy kubectl from master1 to runner node ----------
if should_run kubectl; then
  step_banner "Copying kubectl from master1 to runner"
  install_kubectl
  ok "kubectl copied and available"
else
  info "Skipping step: kubectl"
fi

# ---------- 6) Fetch kubeconfig ----------
if should_run kubeconfig; then
  step_banner "Fetching kubeconfig to ./${KUBECONFIG_DEST}"
  if ! command -v kubectl >/dev/null 2>&1; then
    info "kubectl not found; installing it first (same as kubectl step)"
    install_kubectl
  fi
  source "$GET_KUBECONFIG" "$KUBECONFIG_HOST" "$KUBECONFIG_DEST"
  ok "Kubeconfig written to $(realpath "$KUBECONFIG_DEST")"
else
  info "Skipping step: kubeconfig"
fi

echo -e "\n\033[1;32mAll selected steps completed successfully!\033[0m"