#!/usr/bin/env python3
# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""Let Claude answer the flagship question with the two read-only MCP servers (Anthropic API, claude-opus-5-5).

  DATABASE_URL=postgresql://fleet_reader@host/morlet ANTHROPIC_API_KEY=... \
      python examples/fleet-investigator/agent.py [--question "..."] [--session sessions/run.json]
  python examples/fleet-investigator/agent.py --list-tools        # no API call: start the servers, list their tools

Starts tools/mcp/morlet_mcp.py (recordings) and graph_mcp.py (plant graph) over stdio, offers their tools to the
model, and runs the tool calls it makes until it answers. Both servers are read-only: they connect with
default_transaction_read_only and expose no write tool, so the agent cannot record a finding; a person does that with
confirm_finding.py. The session (question, every tool call with its arguments and result, the answer) is written
as JSON. The API key is read by the SDK from the environment and never printed or written.

Refusal fallback: requests use the API's server-side fallback (fallbacks "default"), so a safety-classifier decline
is retried on a fallback model inside the same call; the session records which model answered.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from mcp.client import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import plant  # noqa: E402

MODEL = "claude-opus-5-5"
SERVERS = {"morletdb": ROOT / "tools/mcp/morlet_mcp.py", "graph": HERE / "graph_mcp.py"}
QUESTION = (f"It is {plant.NOW}. Pump P-102's vibration changed. Have we seen this before, what was it, which other "
            "running assets share the risk (same lot), and what fixed it last time? Cite the recordings (collection, "
            "recording, samples) and the graph elements (ids) you base each statement on, and say what you could not "
            "check.")
SYSTEM = ("You are a reliability engineer's assistant for a plant whose context graph (ISA-95) and vibration "
          "recordings are available through tools. Base every statement on tool results and cite them. Similar "
          "vibration is evidence for a hypothesis, not a diagnosis; say so, and say what was not searched or not "
          "monitored. You cannot change anything: confirming a finding is a person's step outside this session.")
MAX_TURNS = 40
MAX_RESULT_CHARS = 60000


async def open_servers(stack_enter, dsn):
    sessions, tools = {}, []
    for prefix, script in SERVERS.items():
        params = StdioServerParameters(command=sys.executable, args=["-I", str(script)],
                                       env={"DATABASE_URL": dsn, "PATH": os.environ.get("PATH", "")})
        read, write = await stack_enter(stdio_client(params))
        session = await stack_enter(ClientSession(read, write))
        await session.initialize()
        sessions[prefix] = session
        for t in (await session.list_tools()).tools:
            tools.append({"name": f"{prefix}_{t.name}", "description": f"[{prefix} server] {t.description or ''}",
                          "input_schema": t.input_schema})
    return sessions, tools


async def call_tool(sessions, name, args):
    prefix, _, tool = name.partition("_")
    if prefix not in sessions:
        return f"unknown tool {name}", True
    r = await sessions[prefix].call_tool(tool, args)
    text = "\n".join(c.text for c in r.content if getattr(c, "type", "") == "text")
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n[truncated at {MAX_RESULT_CHARS} characters]"
    return text, bool(r.is_error)


async def run(opts):
    from contextlib import AsyncExitStack
    async with AsyncExitStack() as stack:
        sessions, tools = await open_servers(stack.enter_async_context, opts.dsn)
        if opts.list_tools:
            for t in tools:
                print(t["name"])
            return
        import anthropic
        client = anthropic.Anthropic()
        messages = [{"role": "user", "content": opts.question}]
        log = {"model": MODEL, "question": opts.question, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "tools_offered": [t["name"] for t in tools], "turns": []}
        t0 = time.monotonic()
        for _turn in range(MAX_TURNS):
            response = client.beta.messages.create(
                model=MODEL, max_tokens=16000, system=SYSTEM, tools=tools, messages=messages,
                thinking={"type": "adaptive"}, output_config={"effort": "high"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            texts = [b.text for b in response.content if b.type == "text"]
            calls = [b for b in response.content if b.type == "tool_use"]
            turn = {"stop_reason": response.stop_reason, "served_by": response.model, "text": texts,
                    "usage": {"input": response.usage.input_tokens, "output": response.usage.output_tokens},
                    "tool_calls": []}
            log["turns"].append(turn)
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "refusal":
                turn["refusal"] = str(getattr(response, "stop_details", None))
                break
            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason != "tool_use":
                break
            results = []
            for call in calls:
                text, is_error = await call_tool(sessions, call.name, call.input)
                turn["tool_calls"].append({"name": call.name, "input": call.input, "is_error": is_error,
                                           "result": text})
                print(f"> {call.name}({json.dumps(call.input)[:200]}){' ERROR' if is_error else ''}", flush=True)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": text,
                                "is_error": is_error})
            messages.append({"role": "user", "content": results})
        log["elapsed_s"] = round(time.monotonic() - t0, 1)
        log["answer"] = "\n".join(log["turns"][-1]["text"]) if log["turns"] else ""
        print("\n" + log["answer"])
        if opts.session:
            opts.session.parent.mkdir(parents=True, exist_ok=True)
            opts.session.write_text(json.dumps(log, indent=1, default=str) + "\n")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    p.add_argument("--question", default=QUESTION)
    p.add_argument("--session", type=Path, help="write the session as JSON here")
    p.add_argument("--list-tools", action="store_true", help="start the servers and list their tools; no API call")
    opts = p.parse_args(argv)
    if not opts.list_tools and not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set; this runner was not run. The scripted trace "
                         "(investigate.py) answers the same question without a model.")
    asyncio.run(run(opts))


if __name__ == "__main__":
    main()
