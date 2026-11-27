#!/usr/bin/env bash
set -Eeuo pipefail
DEV=${DEV:-}; MNT=${MNT:-/var/lib/monitoring}; ALLOW_FORMAT=${ALLOW_FORMAT:-false}
if mountpoint -q "$MNT"; then
  [[ -n "$DEV" && "$DEV" == /dev/disk/by-id/* ]] || { echo "Set stable expected device even for existing mount"; exit 1; }
  if [[ -n "$DEV" ]]; then
    [[ -b "$DEV" && "$(readlink -f "$DEV")" == "$(readlink -f "$(findmnt -rn -o SOURCE --mountpoint "$MNT")")" ]] || { echo 'Mounted device does not match requested device'; exit 1; }
  fi
  echo "verified existing mount $MNT"; exit 0
fi
[[ -n "$DEV" && -b "$DEV" && "$DEV" == /dev/disk/by-id/* ]] || { echo 'Set an explicit stable device or mount data disk first'; exit 1; }
[[ "$(lsblk -no TYPE "$DEV")" =~ ^(disk|part)$ ]] || { echo "Not a physical disk/partition"; exit 1; }
[[ -z "$(lsblk -nr -o MOUNTPOINTS "$DEV" | tr -d '[:space:]')" ]] || { echo 'Device or child already mounted'; exit 1; }
mkdir -p "$MNT"
[[ -z "$(ls -A "$MNT")" ]] || { echo 'Refusing to hide existing files under mount'; exit 1; }
FSTYPE=$(blkid -o value -s TYPE "$DEV" || true)
if [[ -z "$FSTYPE" ]]; then
  if ! signatures=$(wipefs -n --noheadings -O TYPE "$DEV"); then
    echo "Disk signature inspection failed; refusing to format"; exit 1
  fi
  [[ "$ALLOW_FORMAT" == true && $(lsblk -nr -o NAME "$DEV" | wc -l) -eq 1 && -z "$signatures" ]] || { echo 'Formatting not authorized or device has signatures/children'; exit 1; }
  mkfs.ext4 -L monitoring "$DEV"; FSTYPE=ext4
fi
[[ "$FSTYPE" == ext4 || "$FSTYPE" == xfs ]] || { echo 'Unsupported data filesystem'; exit 1; }
UUID=$(blkid -o value -s UUID "$DEV"); [[ -n "$UUID" ]]
if findmnt --fstab --mountpoint "$MNT" >/dev/null 2>&1; then
  FSTAB_SOURCE=$(findmnt --fstab -rn -o SOURCE --mountpoint "$MNT")
  [[ "$FSTAB_SOURCE" == "UUID=$UUID" || "$(readlink -f "$FSTAB_SOURCE")" == "$(readlink -f "$DEV")" ]] || { echo "Conflicting fstab mount; fix before continuing"; exit 1; }
else
  printf 'UUID=%s %s %s defaults,noatime 0 2\n' "$UUID" "$MNT" "$FSTYPE" >> /etc/fstab
fi
mount "$MNT"
[[ "$(readlink -f "$DEV")" == "$(readlink -f "$(findmnt -rn -o SOURCE --mountpoint "$MNT")")" ]]
echo "mounted $DEV -> $MNT"

