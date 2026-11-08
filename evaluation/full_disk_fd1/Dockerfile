# FD1 (PROTOCOL.md): the release image plus the steward and an entry script that runs PostgreSQL on a fixed-size
# ext4 filesystem (a loop-mounted image in /vol) holding a ballast file. Build from the repository root:
#   docker build -f evaluation/full_disk_fd1/Dockerfile -t morletdb-fd1:local .
FROM ghcr.io/merkhet-io/morletdb:0.3.9-alpha.1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv \
 && python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir "psycopg[binary]==3.2.10" \
 && rm -rf /var/lib/apt/lists/*
COPY tools/steward /opt/steward
COPY evaluation/full_disk_fd1/entry.sh /usr/local/bin/fd1-entry.sh
ENTRYPOINT ["fd1-entry.sh"]
CMD ["postgres"]
