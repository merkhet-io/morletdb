# Test-only image: the MorletDB release image plus TimescaleDB from Timescale's apt repository, for the bridge's
# hypertable tests (test_bridge_timescale.py). Build it locally; never publish it: TimescaleDB's compression and
# continuous aggregates are under the Timescale License, and MorletDB images do not include TimescaleDB.
ARG MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.4.0-beta.7
FROM ${MORLET_IMAGE}
ARG TIMESCALEDB_VERSION=2.32.1~debian12-1712
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
 && curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey | gpg --dearmor -o /usr/share/keyrings/timescaledb.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/timescaledb.gpg] https://packagecloud.io/timescale/timescaledb/debian/ bookworm main" \
    > /etc/apt/sources.list.d/timescaledb.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends "timescaledb-2-postgresql-17${TIMESCALEDB_VERSION:+=$TIMESCALEDB_VERSION}" \
 && rm -rf /var/lib/apt/lists/*
CMD ["postgres", "-c", "shared_preload_libraries=timescaledb", "-c", "timescaledb.telemetry_level=off"]
