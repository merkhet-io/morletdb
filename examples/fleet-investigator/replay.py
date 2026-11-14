#!/usr/bin/env python3
# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""The flagship investigation through the two MCP servers, without a language model.

  DATABASE_URL=postgresql://fleet_reader@host/morlet python examples/fleet-investigator/replay.py [--json]

Starts graph_mcp.py and tools/mcp/morlet_mcp.py over stdio, as an MCP client does, and makes the tool calls an
agent would make for the question in README.md. Prints each call and a short form of its result (--json: all
results as JSON, which the tests use). Run it as fleet_contractor to see what one site sees.
"""
import argparse
import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp.client import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import plant  # noqa: E402

SERVERS = {"graph": HERE / "graph_mcp.py", "morletdb": HERE.parents[1] / "tools/mcp/morlet_mcp.py"}


async def main(dsn, as_json):
    out = []
    async with AsyncExitStack() as stack:
        sessions = {}
        for name, script in SERVERS.items():
            params = StdioServerParameters(command=sys.executable, args=["-I", str(script)],
                                           env={"DATABASE_URL": dsn, "PATH": os.environ.get("PATH", "")})
            read, write = await stack.enter_async_context(stdio_client(params))
            sessions[name] = await stack.enter_async_context(ClientSession(read, write))
            await sessions[name].initialize()

        async def call(server, tool, args):
            r = await sessions[server].call_tool(tool, args)
            text = r.content[0].text
            result = {"error": text} if r.is_error else json.loads(text)
            out.append({"server": server, "tool": tool, "args": args, "result": result})
            if not as_json:
                print(f"\n> {server}.{tool}({json.dumps(args)})\n  {json.dumps(result)[:300]}")
            return result

        await call("graph", "plant_overview", {})
        now = await call("graph", "installed_at", {"equipment_id": "P-102.BRG-3", "utc_time": plant.NOW})
        await call("morletdb", "trend", {"collection": plant.COLLECTION,
                                         "metadata_filter": json.dumps({"equipment_id": "P-102.BRG-3"}),
                                         "start_utc": "2004-03-04T09:00:00Z", "end_utc": plant.NOW,
                                         "max_points": 50})
        hits = await call("graph", "similar_failures", {"equipment_id": "P-102.BRG-3", "utc_time": plant.NOW,
                                                        "top_k": 10})
        if "hits" in hits:
            h = hits["hits"][0]
            await call("morletdb", "describe_interval", {"collection": plant.COLLECTION,
                                                         "recording_id": h["recording_id"],
                                                         "start_sample": h["start_sample"],
                                                         "end_sample": h["end_sample"]})
            if h.get("asset"):
                await call("graph", "failure_history", {"element_id": h["asset"]})
        if now.get("installed"):
            await call("graph", "same_lot", {"asset_id": now["installed"]["asset"]})
        await call("graph", "spare_stock", {})
    if as_json:
        print(json.dumps(out, indent=1))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    p.add_argument("--json", action="store_true")
    o = p.parse_args()
    asyncio.run(main(o.dsn, o.json))
