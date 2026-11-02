#!/usr/bin/env python3
"""Replay the README investigation through the MCP server, without a language model.

  DATABASE_URL=postgresql://agent_reader:...@host/db python examples/agent-demo/replay.py

Starts tools/mcp/morlet_mcp.py over stdio, as Claude Code or Claude Desktop does, and makes the tool calls an
agent would make for the question in README.md. Prints each call and a short form of its result.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp.client import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER = Path(__file__).resolve().parents[2] / "tools/mcp/morlet_mcp.py"
COLLECTION = "public.seismic"
WINDOW = 4096
LEAD = 1352            # start the query window 13.5 s before the located time, so it holds the arrival


async def call(session, name, args):
    print(f"\n> {name}({json.dumps(args)})")
    r = await session.call_tool(name, args)
    text = r.content[0].text
    if r.is_error:
        raise SystemExit(f"tool error: {text}")
    return json.loads(text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-I", str(SERVER)],
                                   env={"DATABASE_URL": os.environ["DATABASE_URL"], "PATH": os.environ.get("PATH", "")})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            cols = await call(session, "list_collections", {})
            for c in cols["collections"]:
                print("  ", {k: c[k] for k in c if k in ("collection", "recordings", "samples", "search")})

            hit = await call(session, "locate_time", {"collection": COLLECTION, "utc_time": "2025-03-14T21:03:30Z",
                                                      "metadata_filter": '{"station": "PB01"}'})
            print("  ", hit)
            rec = hit["matches"][0]
            rid, start = rec["recording_id"], rec["sample"] - LEAD
            q = {"collection": COLLECTION, "recording_id": rid, "start_sample": start, "end_sample": start + WINDOW}

            d = await call(session, "describe_interval", q)
            print("  ", {k: d[k] for k in d if k in ("start_utc", "rms", "peak_abs", "crest_factor", "kurtosis",
                                                       "dominant_frequency_hz")})

            s = await call(session, "search_similar", {**q, "top_k": 5})
            print("   path:", s["path"])
            for h in s["hits"]:
                print("  ", h["recording_id"], h["start_sample"], h["start_utc"], h["distance"])


if __name__ == "__main__":
    asyncio.run(main())
