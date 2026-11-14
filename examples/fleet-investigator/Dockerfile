# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
#
# Fleet investigator database: the MorletDB release image (PostgreSQL 17 with pg_morlet), unchanged, plus Apache AGE
# 1.6.0 for PostgreSQL 17, built from the ASF release source tarball (SHA-512 checked). Build from the repository root:
#   docker build -f examples/fleet-investigator/Dockerfile -t morletdb-fleet-db:local .
#
# AGE needs its parser hook in every session that runs cypher(). `LOAD 'age'` is refused to non-superusers (the
# library is not under $libdir/plugins), so the image preloads it for every backend (CHECKS.md, check 1).

# The release image by digest (the multi-arch index of ghcr.io/merkhet-io/morletdb:0.3.14-alpha.1).
ARG MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.14-alpha.1@sha256:c9ba44b0d980f2dbaf41ee3ecc6aa7a2cf4771fbe194e6d63cd8cfdec8705b34

FROM ${MORLET_IMAGE} AS age-build
# AGE 1.6.0 for PG17: https://archive.apache.org/dist/age/PG17/1.6.0/ (.sha512 published beside it; the .asc
# signature verifies against https://downloads.apache.org/age/KEYS, key 26B6CD9DCD5B0045).
ARG AGE_URL=https://archive.apache.org/dist/age/PG17/1.6.0/apache-age-1.6.0-src.tar.gz
ARG AGE_SHA512=b3021cc6bb0d88a209066596a7f31fb848e4281f2ed719c105f301c7e8017969f5b472fc4b55261105b36ab3dae788e00c4bfff2febe1f147b3b13cddcead2aa
# Headers of exactly the server's version (PG_VERSION of the base image, 17.6-2.pgdg12+1), which the current pgdg
# repository no longer carries; the pgdg archive does, signed with the same key.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && echo "deb [signed-by=/usr/local/share/keyrings/postgres.gpg.asc] https://apt-archive.postgresql.org/pub/repos/apt bookworm-pgdg-archive main" \
      > /etc/apt/sources.list.d/pgdg-archive.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends build-essential flex bison "postgresql-server-dev-17=${PG_VERSION}"
WORKDIR /src
RUN curl -fsSLo age.tar.gz "${AGE_URL}" \
 && echo "${AGE_SHA512}  age.tar.gz" | sha512sum -c - \
 && tar xzf age.tar.gz --strip-components=1 \
 && make with_llvm=no -j"$(nproc)" \
 && make with_llvm=no install DESTDIR=/out

FROM ${MORLET_IMAGE}
COPY --from=age-build /out/usr/lib/postgresql/17/lib/ /usr/lib/postgresql/17/lib/
COPY --from=age-build /out/usr/share/postgresql/17/extension/ /usr/share/postgresql/17/extension/
LABEL org.opencontainers.image.title="MorletDB fleet investigator database (pg_morlet + Apache AGE 1.6.0)"
CMD ["postgres", "-c", "shared_preload_libraries=age"]
