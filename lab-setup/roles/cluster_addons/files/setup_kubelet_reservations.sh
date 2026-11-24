#!/bin/bash
# Set kubelet reservations so the system daemons and the control plane keep
# headroom even when the masters are schedulable.
#
# Why this is declarative now: the reservations were originally applied by hand
# with `kubectl`/ssh and existed nowhere in the repo, so a fresh cluster was born
# with allocatable == capacity. That is exactly the state that let ~12 TrainTicket
# pods (1 Gi each) land on every 12 GB master and starve kubelet + the apiserver
# until master1 went NotReady (2026-11-17).
#
# allocatable = capacity - kubeReserved - systemReserved - evictionHard.
#
# Idempotent: it rewrites only the three keys, leaves everything else in the
# kubelet config untouched, and only restarts kubelet when the file actually
# changed. Rolls back if the kubelet does not come back.
#
# Environment:
#   KCFG           kubelet config file      (default /etc/kubernetes/kubelet-config.yaml)
#   KUBE_CPU       kubeReserved cpu         (default 1)
#   KUBE_MEM       kubeReserved memory      (default 1536Mi)
#   SYS_CPU        systemReserved cpu       (default 500m)
#   SYS_MEM        systemReserved memory    (default 1Gi)
#   EVICT_MEM      evictionHard memory.available (default 500Mi)
set -u

KCFG=${KCFG:-/etc/kubernetes/kubelet-config.yaml}
KUBE_CPU=${KUBE_CPU:-1}
KUBE_MEM=${KUBE_MEM:-1536Mi}
SYS_CPU=${SYS_CPU:-500m}
SYS_MEM=${SYS_MEM:-1Gi}
EVICT_MEM=${EVICT_MEM:-500Mi}

[ -f "$KCFG" ] || { echo "missing $KCFG"; exit 1; }

HOST=$(hostname)
echo "=== $HOST: patching $KCFG"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found -- cannot patch $KCFG"
  exit 1
fi

# Compare the desired state against the current one BEFORE touching the file, so
# a no-op run does not restart the kubelet.
CUR=$(python3 - "$KCFG" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
def g(*path):
    cur = d
    for p in path:
        if not isinstance(cur, dict): return None
        cur = cur.get(p)
    return cur
print("%s|%s|%s|%s|%s" % (
    g('kubeReserved','cpu'), g('kubeReserved','memory'),
    g('systemReserved','cpu'), g('systemReserved','memory'),
    g('evictionHard','memory.available')))
PY
)
WANT="${KUBE_CPU}|${KUBE_MEM}|${SYS_CPU}|${SYS_MEM}|${EVICT_MEM}"

echo "  current: $CUR"
echo "  desired: $WANT"

if [ "$CUR" = "$WANT" ]; then
  echo "  already correct -- no change, no restart"
  exit 0
fi

cp -n "$KCFG" "${KCFG}.pre_reservation" 2>/dev/null || true

python3 - "$KCFG" "$KUBE_CPU" "$KUBE_MEM" "$SYS_CPU" "$SYS_MEM" "$EVICT_MEM" <<'PY'
import sys, yaml
path, kcpu, kmem, scpu, smem, evict = sys.argv[1:7]
d = yaml.safe_load(open(path)) or {}
d['kubeReserved'] = {'cpu': str(kcpu), 'memory': str(kmem)}
d['systemReserved'] = {'cpu': str(scpu), 'memory': str(smem)}
eh = d.get('evictionHard') or {}
eh['memory.available'] = str(evict)
eh.setdefault('nodefs.available', '10%')
eh.setdefault('imagefs.available', '15%')
d['evictionHard'] = eh
yaml.safe_dump(d, open(path, 'w'), sort_keys=True, default_flow_style=False)
print("  patched")
PY
[ $? -eq 0 ] || { echo "  PATCH FAILED"; exit 1; }

echo "  restarting kubelet"
systemctl restart kubelet

# Give the kubelet time to register; verify it is active before declaring success.
ok=0
for _ in $(seq 1 15); do
  if systemctl is-active --quiet kubelet; then ok=1; break; fi
  sleep 4
done

if [ "$ok" != "1" ]; then
  echo "  kubelet did NOT come back -- rolling back"
  [ -f "${KCFG}.pre_reservation" ] && cp -f "${KCFG}.pre_reservation" "$KCFG"
  systemctl restart kubelet
  echo "  ROLLED BACK"
  exit 1
fi

echo "  kubelet active"
exit 0