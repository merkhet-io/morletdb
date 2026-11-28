ARG MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.4.0-beta.3@sha256:897d9b0e62e7320cdcdbbde823f9de33aa6368d6f0461a3b43f08fc0861b095e
FROM ${MORLET_IMAGE} AS build
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git ca-certificates libssl-dev postgresql-server-dev-17 \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch 2.31.0 https://github.com/timescale/timescaledb.git /tmp/timescaledb
WORKDIR /tmp/timescaledb
RUN test "$(git rev-parse HEAD)" = 4fa2a7c6b579171a1a8966fd87a0d4189ebab43b && \
    git rev-parse HEAD > /timescaledb-source-revision && \
    ./bootstrap -DCMAKE_BUILD_TYPE=Release -DREGRESS_CHECKS=OFF -DWARNINGS_AS_ERRORS=OFF \
    -DPG_CONFIG=/usr/lib/postgresql/17/bin/pg_config && \
    cmake --build build -j 4 && DESTDIR=/out cmake --install build
FROM ${MORLET_IMAGE}
COPY --from=build /out/ /
COPY --from=build /timescaledb-source-revision /usr/share/doc/timescaledb-source-revision
CMD ["postgres", "-c", "shared_preload_libraries=timescaledb"]
