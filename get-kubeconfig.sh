#!/bin/bash

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "This script must be sourced so it can export KUBECONFIG and set alias."
  echo "Usage: source $0 [ssh-host] [output-file]"
  return 1 2>/dev/null || exit 1
fi

set -Eeuo pipefail

HOST="${1:-master1}"
OUT="${2:-admin.conf}"
TUNNEL_PORT="${KUBECTL_TUNNEL_PORT:-16443}"
TUNNEL_PID_FILE="${TUNNEL_PID_FILE:-/tmp/kubeconfig-tunnel-${HOST}-${TUNNEL_PORT}.pid}"

command -v ssh >/dev/null || { echo "ssh not found"; return 1; }
command -v kubectl >/dev/null || { echo "kubectl not found"; return 1; }

TMP_OUT="${OUT}.tmp.$$"
rm -f -- "$TMP_OUT"
if ! ssh ${SSH_OPTS:-} "$HOST" 'sudo cat /etc/kubernetes/admin.conf' > "$TMP_OUT"; then
  echo "ERROR: ssh fetch from $HOST failed; previous $OUT left untouched"
  rm -f -- "$TMP_OUT"
  return 1
fi

if [[ ! -s "$TMP_OUT" ]]; then
  echo "ERROR: fetched kubeconfig is empty; previous $OUT left untouched"
  rm -f -- "$TMP_OUT"
  return 1
fi
chmod 600 "$TMP_OUT"
mv -f -- "$TMP_OUT" "$OUT"

ORIG_SERVER="$(grep -m1 '[[:space:]]server:' "$OUT" | awk '{print $2}')"
ORIG_HOSTPORT="${ORIG_SERVER#https://}"; ORIG_HOSTPORT="${ORIG_HOSTPORT%%/*}"
ORIG_ADDR="${ORIG_HOSTPORT%%:*}"; ORIG_PORT="${ORIG_HOSTPORT##*:}"

tcp_probe() {
  timeout 3 bash -c "</dev/tcp/$1/$2" 2>/dev/null
}

MODE=direct
if tcp_probe "$ORIG_ADDR" "$ORIG_PORT"; then
  echo "apiserver reachable directly at $ORIG_HOSTPORT — using kubeconfig as-is"
else
  MODE=tunnel
  echo "apiserver $ORIG_ADDR:$ORIG_PORT not reachable from this host (expected when the" \
       "runner is outside the cluster experiment LAN) — using ssh tunnel via $HOST"

  if [[ -f "$TUNNEL_PID_FILE" ]] && kill -0 "$(cat "$TUNNEL_PID_FILE")" 2>/dev/null; then
    echo "ssh tunnel already running (pid $(cat "$TUNNEL_PID_FILE"))"
  else
    pkill -f "ssh .*-N .*-L ${TUNNEL_PORT}:${ORIG_HOSTPORT}" 2>/dev/null || true
    rm -f -- "$TUNNEL_PID_FILE"
    nohup ssh ${SSH_OPTS:-} -N \
      -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=6 \
      -o StrictHostKeyChecking=accept-new \
      -L "127.0.0.1:${TUNNEL_PORT}:${ORIG_HOSTPORT}" \
      "$HOST" >/tmp/kubeconfig-tunnel-${HOST}-${TUNNEL_PORT}.log 2>&1 &
    echo $! > "$TUNNEL_PID_FILE"
    disown
    echo "started ssh tunnel (pid $(cat "$TUNNEL_PID_FILE")) -> 127.0.0.1:${TUNNEL_PORT} -> $ORIG_HOSTPORT"
  fi

  TUNNEL_UP=0
  for _ in $(seq 1 15); do
    if tcp_probe 127.0.0.1 "$TUNNEL_PORT"; then
      TUNNEL_UP=1
      break
    fi
    sleep 1
  done
  if [[ "$TUNNEL_UP" != 1 ]]; then
    echo "ERROR: ssh tunnel to $HOST (127.0.0.1:$TUNNEL_PORT -> $ORIG_HOSTPORT) did not come up"
    echo "      log: /tmp/kubeconfig-tunnel-${HOST}-${TUNNEL_PORT}.log"
    return 1
  fi

  sed -i -E "s#^[[:space:]]*server:.*#    server: https://127.0.0.1:${TUNNEL_PORT}#" "$OUT"
  echo "kubeconfig server rewritten to https://127.0.0.1:${TUNNEL_PORT} (TLS SANs cover 127.0.0.1)"
fi

export KUBECONFIG="$PWD/$OUT"
alias k=kubectl

echo "KUBECONFIG set to: $KUBECONFIG (mode: $MODE)"
kubectl cluster-info || {
  echo "kubectl cluster-info failed; check reachability / tunnel / certs."
  return 1
}

echo "Tip: add these to your ~/.bashrc for future shells:"
echo "  export KUBECONFIG=\"$PWD/$OUT\""
echo "  alias k=kubectl"
if [[ "$MODE" == tunnel ]]; then
  echo "  (the ssh tunnel is managed by get-kubeconfig.sh; re-source it after reboots)"
fi
