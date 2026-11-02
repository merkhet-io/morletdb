# Tools image for the machine demo: the importer, the MCP server and bsdtar for the NASA archive.
FROM python:3.12.10-slim-bookworm
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libarchive-tools && rm -rf /var/lib/apt/lists/*
COPY tools/ingest/requirements.txt tools/ingest/requirements.txt
COPY tools/mcp/requirements.txt tools/mcp/requirements.txt
RUN pip install --no-cache-dir -r tools/ingest/requirements.txt -r tools/mcp/requirements.txt
COPY tools/ingest tools/ingest
COPY tools/mcp tools/mcp
COPY examples/machine-demo examples/machine-demo
ENV PYTHONUNBUFFERED=1
