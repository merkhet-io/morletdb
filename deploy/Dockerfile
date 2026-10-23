# Patch versions are fixed; image tags are not content-addressed. Record digests
# in a release manifest before claiming bit-for-bit reproducible release builds.
FROM postgres:17.6-bookworm AS postgres
FROM rust:1.91.1-bookworm AS builder
RUN apt-get update && apt-get install -y --no-install-recommends clang libclang-dev libssl-dev pkg-config && rm -rf /var/lib/apt/lists/*
COPY --from=postgres /usr/lib/postgresql/17 /usr/lib/postgresql/17
COPY --from=postgres /usr/include/postgresql /usr/include/postgresql
COPY --from=postgres /usr/share/postgresql/17 /usr/share/postgresql/17
RUN cargo install cargo-pgrx --version 0.16.1 --locked
RUN cargo pgrx init --pg17 /usr/lib/postgresql/17/bin/pg_config
WORKDIR /src
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY .cargo .cargo
COPY crates crates
COPY extensions extensions
COPY testdata testdata
WORKDIR /src/extensions/pg_morlet
RUN cargo pgrx package --release --pg-config /usr/lib/postgresql/17/bin/pg_config

FROM postgres:17.6-bookworm
COPY --from=builder /src/target/release/pg_morlet-pg17/usr/lib/postgresql/17/lib/ /usr/lib/postgresql/17/lib/
COPY --from=builder /src/target/release/pg_morlet-pg17/usr/share/postgresql/17/extension/ /usr/share/postgresql/17/extension/
