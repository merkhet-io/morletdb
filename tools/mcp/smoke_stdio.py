#!/usr/bin/env python3
"""Start morlet_mcp.py over stdio like an MCP client does, list its tools and run an investigation's steps.

  DATABASE_URL=... python tools/mcp/smoke_stdio.py <collection> <recording_id> <start_sample> [window]
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp.client import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER = Path(__file__).resolve().parent / "morlet_mcp.py"


async def main(collection, recording_id, start, window=4096):
    params = StdioServerParameters(command=sys.executable, args=["-I", str(SERVER)],
                                   env={"DATABASE_URL": os.environ["DATABASE_URL"], "PATH": os.environ.get("PATH", "")})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("instructions:", (session.instructions or "")[:80], "...")
            print("tools:", [t.name for t in (await session.list_tools()).tools])
            steps = [("list_collections", {}),
                     ("describe_interval", {"collection": collection, "recording_id": recording_id,
                                            "start_sample": start, "end_sample": start + window}),
                     ("search_similar", {"collection": collection, "recording_id": recording_id,
                                         "start_sample": start, "end_sample": start + window, "top_k": 5}),
                     ("list_recordings", {"collection": "public.does_not_exist"})]
            for name, args in steps:
                r = await session.call_tool(name, args)
                text = r.content[0].text
                print(f"{name}: {'ERROR ' if r.is_error else ''}{text[:400]}")


if __name__ == "__main__":
    a = sys.argv[1:]
    asyncio.run(main(a[0], a[1], int(a[2]), int(a[3]) if len(a) > 3 else 4096))
