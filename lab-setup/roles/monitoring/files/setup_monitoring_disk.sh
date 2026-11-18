#!/bin/bash
# Prepare the disk that holds the monitoring data (Mimir / Loki / Tempo / Grafana).
#
# The role used to only create directories under this path, so on a fresh host
# Mimir and Loki silently wrote into the root filesystem and filled it. This
# formats and mounts the dedicated disk when DEV is given, and is idempotent:
# it only touches the disk when there is no filesystem signature yet.
#
# Environment:
#   DEV    block device to use (e.g. /dev/sdb). Empty = do not format or mount;
#          just verify the path is already a mountpoint.
#   MNT    mount point            (default /var/lib/monitoring)
#   LABEL  filesystem label       (default monitoring)
set -u

DEV=${DEV:-}
MNT=${MNT:-/var/lib/monitoring}
LABEL=${LABEL:-monitoring}

say() { echo "$*"; }

if [ -z "$DEV" ]; then
  # No dedicated block device: the path may legitimately be a directory on an
  # existing mount (e.g. /share/monitoring on the 500 G NFS mount). Create it
  # and report which filesystem it lands on; only the root filesystem is worth
  # a warning, and even then it is not fatal -- a big local root disk is a
  # valid choice.
  mkdir -p "$MNT"
  SRC=$(df --output=source "$MNT" 2>/dev/null | tail -1 | tr -d ' ')
  ROOTSRC=$(df --output=source / 2>/dev/null | tail -1 | tr -d ' ')
  if [ "$SRC" = "$ROOTSRC" ]; then
    say "WARNING: $MNT is on the root filesystem ($SRC); make sure it has room for the retention window"
  else
    say "using existing mount $SRC for $MNT"
  fi
  exit 0
fi

if [ ! -b "$DEV" ]; then
  say "block device $DEV does not exist"
  exit 1
fi

if findmnt -rn --target "$MNT" >/dev/null 2>&1; then
  say "already-mounted $MNT ($(findmnt -rn -o SOURCE --target "$MNT"))"
  exit 0
fi

mkdir -p "$MNT"

FSTYPE=$(blkid -o value -s TYPE "$DEV" 2>/dev/null || true)
if [ -z "$FSTYPE" ]; then
  say "formatting $DEV as ext4 (label=$LABEL)"
  mkfs.ext4 -q -L "$LABEL" "$DEV" || { say "mkfs failed on $DEV"; exit 1; }
else
  say "$DEV already has a filesystem ($FSTYPE) -- not reformatting"
fi

mount "$DEV" "$MNT" || { say "mount $DEV -> $MNT failed"; exit 1; }
say "mounted $DEV -> $MNT"

# fstab by label: stable across device renumbering, which matters when the same
# host later gets more disks.
if ! grep -qE "^[[:space:]]*LABEL=${LABEL}[[:space:]]" /etc/fstab; then
  echo "LABEL=${LABEL} ${MNT} ext4 defaults,noatime 0 2" >> /etc/fstab
  say "added fstab entry LABEL=$LABEL"
fi

exit 0