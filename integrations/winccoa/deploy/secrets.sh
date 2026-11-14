#!/bin/sh
# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
#
# Generate the secrets of the compose deployment in integrations/winccoa/secrets/ (mode 0700 directory, 0600 files),
# once. Nothing is printed but token fingerprints. Then: export MORLET_UID=$(id -u) MORLET_GID=$(id -g)
set -eu
dir="$(cd "$(dirname "$0")/.." && pwd)/secrets"
if [ -e "$dir" ]; then echo "$dir exists; remove it to start over" >&2; exit 1; fi
umask 077
mkdir "$dir"
hex() { od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'; }
admin=$(hex 24); companion=$(hex 24); token=$(hex 32)
printf '%s' "$admin" > "$dir/db_admin_password"
printf '%s\n' "$token" > "$dir/winccoa_token"
printf '%s' "$companion" > "$dir/companion_password"
{
  printf '127.0.0.1:54340:morlet:morlet:%s\n' "$admin"
  printf '127.0.0.1:54340:morlet:winccoa_companion:%s\n' "$companion"
} > "$dir/pgpass"
fp=$(printf '%s' "$token" | (sha256sum 2>/dev/null || shasum -a 256) | cut -c1-8)
echo "secrets written to $dir; adapter token fingerprint $fp"
