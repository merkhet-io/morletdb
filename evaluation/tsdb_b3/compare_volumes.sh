#!/usr/bin/env bash
# File-by-file comparison of two CedarDB data volumes (read-only mounts): names, sizes, checksums, and for files of
# equal size the number of differing bytes.
set -euo pipefail
IMG=cedardb/cedardb@sha256:737ce1bf2ef3c9f2ba7e599ae4b2a0ae0399125e3f63a417a51141976a40518e
docker run --rm --entrypoint bash -v "$1":/a:ro -v "$2":/b:ro "$IMG" -c '
cd /a; find . -type f | sort > /tmp/fa; cd /b; find . -type f | sort > /tmp/fb
echo "only in A: $(comm -23 /tmp/fa /tmp/fb | wc -l), only in B: $(comm -13 /tmp/fa /tmp/fb | wc -l), common: $(comm -12 /tmp/fa /tmp/fb | wc -l)"
comm -23 /tmp/fa /tmp/fb | head -20 | sed "s/^/  only A /"; comm -13 /tmp/fa /tmp/fb | head -20 | sed "s/^/  only B /"
same=0; diff=0
for f in $(comm -12 /tmp/fa /tmp/fb); do
  sa=$(stat -c %s "/a/$f"); sb=$(stat -c %s "/b/$f")
  if cmp -s "/a/$f" "/b/$f"; then same=$((same+1))
  else diff=$((diff+1)); if [ "$sa" = "$sb" ]; then n=$(cmp -l "/a/$f" "/b/$f" | wc -l); echo "  differs $f size $sa, $n bytes differ"
  else echo "  differs $f size $sa vs $sb"; fi; fi
done
echo "identical: $same, different: $diff"'
