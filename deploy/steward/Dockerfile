# morlet-steward as a sidecar container (docs/steward-design.md, #287). Mount the database's data volume read-only
# at /data so the steward can read free disk space, and pass MORLET_STEWARD_DSN for the morlet_steward role.
FROM python:3.12.10-slim-bookworm
RUN pip install --no-cache-dir "psycopg[binary]==3.2.10"
COPY tools/steward/policy.py tools/steward/store.py tools/steward/steward.py /opt/steward/
WORKDIR /opt/steward
USER nobody
ENTRYPOINT ["python", "steward.py"]
CMD ["run", "--verbose", "--data-path", "/data"]
# healthy while the status is normal or attention; unhealthy when action is required or the steward stopped
HEALTHCHECK --interval=60s --timeout=20s --start-period=60s CMD python steward.py check; test $? -lt 2
