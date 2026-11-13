#!/bin/bash
# Mount the fixed-size data filesystem (created on first start, with a ballast file), free the ballast when asked
# (/vol/free-ballast), then start PostgreSQL on it with the image's own entrypoint.
set -euo pipefail
IMG=/vol/disk.img MNT=/mnt/disk
if [ ! -f "$IMG" ]; then
  dd if=/dev/zero of="$IMG" bs=1M count="${FD1_DISK_MB:-600}" status=none
  mkfs.ext4 -q -F -m 0 "$IMG"
  NEW=1
fi
mkdir -p "$MNT"
mount -o loop "$IMG" "$MNT"
if [ -n "${NEW:-}" ]; then dd if=/dev/zero of="$MNT/ballast" bs=1M count="${FD1_BALLAST_MB:-64}" status=none; sync; fi
if [ -f /vol/free-ballast ]; then rm -f "$MNT/ballast" "$MNT/filler" /vol/free-ballast; sync; echo "fd1: ballast freed"; fi
mkdir -p "$MNT/pgdata"; chown postgres:postgres "$MNT/pgdata"; chmod 700 "$MNT/pgdata"
export PGDATA="$MNT/pgdata"
exec docker-entrypoint.sh "$@"
