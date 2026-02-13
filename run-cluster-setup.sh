#!/usr/bin/env bash
# Orchestrates full cluster setup end-to-end
# Steps:
#  1) lab-setup/preparing.yaml          (name: preparing)
#  2) lab-setup/haproxy-lb.yaml         (name: haproxy)
#  3) kubespray cluster.yml (with venv) (name: kubespray)
#  4) lab-setup/postcluster.yaml        (name: postcluster)
#  5) get kubeconfig into ./admin.conf  (name: kubeconfig)

set -Eeuo pipefail

# ---------- config ----------
LAB_DIR="lab-setup"
KUBESPRAY_DIR="kubespray"

KS_DIR_ABS="$(cd "$KUBESPRAY_DIR" && pwd)"
KS_VENV="${KS_DIR_ABS}/.venv"
KS_REQUIREMENTS="${KS_DIR_ABS}/requirements.txt"

KUBECONFIG_DEST="admin.conf"

ANSIBLE_VAULT_FLAG=${ANSIBLE_VAULT_PASSWORD_FILE:+--vault-password-file "$ANSIBLE_VAULT_PASSWORD_FILE"}

# ---------- helpers ----------
ok()   { echo -e "\033[1;32m✔ $*\033[0m"; }
info() { echo -e "\033[1;34mℹ $*\033[0m"; }
err()  { echo -e "\033[1;31m✘ $*\033[0m"; }

step_banner() {
  echo -e "\n\033[1;36m==> $*\033[0m"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { err "Missing required command: $1"; exit 127; }
}

usage() {
  cat <<EOF
Usage: $0 [--start-from=STEP] | [STEP]

Steps (in order):
  preparing   - run lab-setup/preparing.yaml
  haproxy     - run lab-setup/haproxy-lb.yaml
  kubespray   - deploy cluster with kubespray (venv + cluster.yml)
  postcluster - run lab-setup/postcluster.yaml
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
    preparing|haproxy|kubespray|postcluster|kubeconfig) START_FROM="$1" ;;
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

export ANSIBLE_SSH_CONTROL_PATH_DIR=/tmp/ansible-cp
mkdir -p /tmp/ansible-cp
chmod 700 /tmp/ansible-cp

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

  if [[ ! -d "$KS_VENV" ]]; then
    info "Creating Kubespray virtualenv at $KS_VENV"
    python3 -m venv "$KS_VENV"
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

# ---------- 5) Fetch kubeconfig ----------
if should_run kubeconfig; then
  step_banner "Fetching kubeconfig from Kubespray artifacts -> ./${KUBECONFIG_DEST}"

  ARTIFACT_KUBECONFIG="$(find "${KS_DIR_ABS}/inventory" -path "*/artifacts/admin.conf" -print -quit)"

  if [[ ! -s "$ARTIFACT_KUBECONFIG" ]]; then
    err "Kubespray kubeconfig artifact not found or empty: $ARTIFACT_KUBECONFIG"
    err "Make sure kubeconfig_localhost: true and Kubespray completed successfully."
    exit 1
  fi

  cp -f "$ARTIFACT_KUBECONFIG" "$KUBECONFIG_DEST"
  chmod 600 "$KUBECONFIG_DEST"

  export KUBECONFIG="$PWD/$KUBECONFIG_DEST"
  ok "Kubeconfig written to $(realpath "$KUBECONFIG_DEST")"
  info "Tip: export KUBECONFIG=$KUBECONFIG"
else
  info "Skipping step: kubeconfig"
fi

echo -e "\n\033[1;32mAll selected steps completed successfully!\033[0m"