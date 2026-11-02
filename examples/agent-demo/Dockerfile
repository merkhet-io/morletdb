# Tools image for the agent demo: the importer (with ObsPy for miniSEED) and the MCP server.
FROM python:3.12.10-slim-bookworm
WORKDIR /app
COPY tools/ingest/requirements.txt tools/ingest/requirements.txt
COPY tools/mcp/requirements.txt tools/mcp/requirements.txt
# ObsPy has no wheel for every platform (linux/arm64 builds from source), hence the temporary compiler;
# it imports pkg_resources, which setuptools 81 and later no longer ship.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
 && pip install --no-cache-dir -r tools/ingest/requirements.txt -r tools/mcp/requirements.txt obspy==1.4.2 setuptools==75.8.0 \
 && apt-get purge -y gcc libc6-dev && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY tools/ingest tools/ingest
COPY tools/mcp tools/mcp
COPY examples/agent-demo examples/agent-demo
ENV PYTHONUNBUFFERED=1
