#!/usr/bin/env python3
"""Targeted discovery using the same registered contracts as the MCP server."""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    operations = commands.add_parser("operations", help="List operations without their full schemas")
    explain = commands.add_parser("explain", help="Describe one operation or schema field")
    explain.add_argument("name")
    explain.add_argument("--path", default="", help="JSON Pointer, e.g. /input_schema/properties/top_k")
    describe = commands.add_parser("describe", help="Inspect live collection capabilities using DATABASE_URL")
    describe.add_argument("collection")
    call = commands.add_parser("call", help="Execute a registered read-only MCP tool using DATABASE_URL")
    call.add_argument("name")
    call.add_argument("--input", required=True, help="JSON arguments file; - reads stdin (maximum 2 MB)")
    for command in (operations, explain, describe, call):
        command.add_argument("--json", action="store_true", help="JSON output (the default)")
    args = parser.parse_args()

    spec = importlib.util.spec_from_file_location("morlet_mcp", Path(__file__).with_name("morlet_mcp.py"))
    server = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    try:
        if args.command == "operations":
            result = asyncio.run(server.list_operations())
        elif args.command == "explain":
            result = asyncio.run(server.describe_operation(args.name, args.path))
        elif args.command == "describe":
            result = server.describe_collection(args.collection)
        else:
            def read_arguments():
                if args.input == "-":
                    raw = sys.stdin.buffer.read(2_000_001)
                else:
                    with open(args.input, "rb") as source:
                        raw = source.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError("arguments exceed 2000000 bytes")
                arguments = json.loads(raw)
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be a JSON object")
                json.dumps(arguments, allow_nan=False)
                return arguments
            arguments = read_arguments()
            tools = asyncio.run(server.mcp.list_tools())
            tool = next((t for t in tools if t.name == args.name), None)
            if tool is None or not tool.annotations or not tool.annotations.read_only_hint:
                raise ValueError("unknown or non-read-only operation; use operations to discover names")
            response = asyncio.run(server.mcp.call_tool(args.name, arguments))
            if response.is_error:
                print(json.dumps({"error": [c.model_dump(mode="json") for c in response.content]}), file=sys.stderr)
                return 1
            payload = response.structured_content
            if payload is None:
                payload = {"content": [c.model_dump(mode="json") for c in response.content]}
            print(json.dumps(payload, ensure_ascii=True, allow_nan=False, indent=2))
            return 0
    except (server.ToolError, ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
