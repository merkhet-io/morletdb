#!/usr/bin/env bash
# Combine the tested architecture images of the extension-only image (#315) for one PostgreSQL major into its
# multi-architecture tag, <VERSION>-pg<MAJOR>, and write digest, name and sha to GITHUB_OUTPUT (release workflow).
#   deploy/extension_index.sh MAJOR
set -euo pipefail
MAJOR=$1
image="ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/pg_morlet"
tag="$(cat VERSION)-pg$MAJOR"
docker buildx imagetools create -t "$image:$tag" "$image:$tag-amd64" "$image:$tag-arm64"
digest=$(docker buildx imagetools inspect "$image:$tag" --format '{{json .Manifest}}' | jq -r .digest)
docker buildx imagetools inspect "$image:$tag"
{ echo "digest=$image@$digest"; echo "name=$image"; echo "sha=$digest"; } >> "$GITHUB_OUTPUT"
