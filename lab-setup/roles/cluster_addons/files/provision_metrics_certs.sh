#!/usr/bin/env bash
# Provision a dedicated metrics CA + per-master serving certificates for the
# kube-scheduler (10259) and kube-controller-manager (10257) secure metrics
# endpoints, and distribute the PUBLIC CA bundle to every Kubernetes host at
# /etc/kubernetes/metrics-ca/{scheduler,controller_manager}.crt.
#
# Runs from the loadgen (has ssh to all nodes). Idempotent: re-running regenerates
# certs and re-applies manifest flags. Backs up static-pod manifests first.
# No private keys are printed; the CA key stays on the loadgen (0600).
set -euo pipefail
: "${K8S_NODES:=master1 master2 master3 worker1 worker2 worker3}"
: "${K8S_MASTERS:=master1 master2 master3}"
declare -A MASTER_IP=( [master1]=10.10.1.3 [master2]=10.10.1.4 [master3]=10.10.1.5 )
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -subj "/CN=lab-metrics-ca" -days 3650 >/dev/null 2>&1
mkcert() { local name=$1 ip=$2; openssl req -newkey rsa:2048 -nodes -keyout "$name.key" -out "$name.csr" -subj "/CN=$name" >/dev/null 2>&1
  printf "subjectAltName=IP:$ip,IP:127.0.0.1,DNS:$name,DNS:localhost\n" > "$name.ext"
  openssl x509 -req -in "$name.csr" -CA ca.crt -CAkey ca.key -CAcreateserial -out "$name.crt" -days 365 -extfile "$name.ext" >/dev/null 2>&1; }
for m in "${!MASTER_IP[@]}"; do
  mkcert "scheduler-$m" "${MASTER_IP[$m]}"; mkcert "cm-$m" "${MASTER_IP[$m]}"
  ssh "$m" 'sudo mkdir -p /etc/kubernetes/pki/metrics /etc/kubernetes/metrics-ca'
  scp -q "scheduler-$m.crt" "$m:/tmp/s.crt"; scp -q "scheduler-$m.key" "$m:/tmp/s.key"
  scp -q "cm-$m.crt" "$m:/tmp/c.crt"; scp -q "cm-$m.key" "$m:/tmp/c.key"
  ssh "$m" 'sudo mv /tmp/s.crt /etc/kubernetes/pki/metrics/scheduler.crt; sudo mv /tmp/s.key /etc/kubernetes/pki/metrics/scheduler.key; sudo mv /tmp/c.crt /etc/kubernetes/pki/metrics/controller-manager.crt; sudo mv /tmp/c.key /etc/kubernetes/pki/metrics/controller-manager.key'
  # wire manifest flags (idempotent)
  ssh "$m" 'sudo bash -s' <<'EOS'
cd /etc/kubernetes/manifests
cp -n kube-scheduler.yaml kube-scheduler.yaml.bak 2>/dev/null || true
cp -n kube-controller-manager.yaml kube-controller-manager.yaml.bak 2>/dev/null || true
python3 - <<'PY'
import re
for f,comp,cert in [('kube-scheduler.yaml','kube-scheduler','scheduler'),('kube-controller-manager.yaml','kube-controller-manager','controller-manager')]:
    s=open(f).read()
    if '--tls-cert-file' in s: continue
    flags=f"    - --tls-cert-file=/etc/kubernetes/pki/metrics/{cert}.crt\n    - --tls-private-key-file=/etc/kubernetes/pki/metrics/{cert}.key\n"
    s=re.sub(r'(    - '+re.escape(comp)+r'\n)', r'\1'+flags, s, count=1); open(f,'w').write(s)
PY
EOS
done
for h in $K8S_NODES; do
  ssh "$h" 'sudo mkdir -p /etc/kubernetes/metrics-ca'
  scp -q ca.crt "$h:/tmp/ca.crt"
  ssh "$h" 'sudo mv /tmp/ca.crt /etc/kubernetes/metrics-ca/scheduler.crt; sudo cp /etc/kubernetes/metrics-ca/scheduler.crt /etc/kubernetes/metrics-ca/controller_manager.crt'
done
echo "metrics certs provisioned on $K8S_NODES"