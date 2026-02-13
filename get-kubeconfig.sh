#!/usr/bin/env bash
# Usage: source get-kubeconfig.sh [ssh-host] [output-file]
# Example: source get-kubeconfig.sh master1 admin.conf
# Optional: SSH_OPTS="-J bastion -i ~/.ssh/id_rsa -p 2222"

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "This script must be sourced so it can export KUBECONFIG and set alias."
  echo "Usage: source $0 [ssh-host] [output-file]"
  return 1 2>/dev/null || exit 1
fi

set -Eeuo pipefail

HOST="${1:-master1}"
OUT="${2:-admin.conf}"

command -v ssh >/dev/null || { echo "ssh not found"; return 1; }
command -v kubectl >/dev/null || { echo "kubectl not found"; return 1; }

rm -f -- "$OUT"
ssh ${SSH_OPTS:-} "$HOST" 'sudo cat /etc/kubernetes/admin.conf' > "$OUT"

if [[ ! -s "$OUT" ]]; then
  echo "ERROR: fetched kubeconfig is empty ($OUT)"
  return 1
fi
chmod 600 "$OUT"

export KUBECONFIG="$PWD/$OUT"
alias k=kubectl

echo "KUBECONFIG set to: $KUBECONFIG"
kubectl cluster-info || {
  echo "kubectl cluster-info failed; check reachability / HAProxy / certs."
  return 1
}

echo "Tip: add these to your ~/.bashrc for future shells:"
echo "  export KUBECONFIG=\"$PWD/$OUT\""
echo "  alias k=kubectl"