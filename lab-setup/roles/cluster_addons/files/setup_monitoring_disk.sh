#!/bin/bash
# Provision the spare second disk on a control-plane node for monitoring data.
#
# Why: Loki and Mimir run on the control-plane nodes (never on a worker, which
# is also a benchmark target). The masters carry a second, unmounted disk; using
# it keeps monitoring I/O off the disk that holds etcd and the container runtime.
#
# Idempotent: formats only when the device has no filesystem signature and the
# mount point is not already mounted.
set -euo pipefail
DEV=${DEV:-/dev/sdb}
MNT=${MNT:-/var/lib/monitoring}

if mountpoint -q "$MNT"; then
  echo "$(hostname): $MNT already mounted -- nothing to do"
  exit 0
fi

if blkid "$DEV" >/dev/null 2>&1; then
  echo "$(hostname): REFUSING -- $DEV already carries a filesystem signature" >&2
  blkid "$DEV" >&2
  exit 1
fi

mkfs.ext4 -F -L monitoring "$DEV"
mkdir -p "$MNT"
UUID=$(blkid -s UUID -o value "$DEV")
grep -q " $MNT " /etc/fstab || echo "UUID=$UUID $MNT ext4 defaults,noatime 0 2" >> /etc/fstab
mount "$MNT"
mkdir -p "$MNT/local-path-provisioner"
chmod 0777 "$MNT/local-path-provisioner"
echo "$(hostname): mounted $DEV -> $MNT"
df -h "$MNT" | tail -1