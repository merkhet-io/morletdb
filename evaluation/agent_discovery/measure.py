#!/usr/bin/env python3
"""D1: deterministic schema context counts, not an LLM task-success evaluation. See PROTOCOL.md."""
import argparse
import ast
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def serialize(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def source_state(root):
    """HEAD identifies the base; hashes identify working files when source_dirty is true.

    Include staged, unstaged and untracked changes; sample before writing the new artifact.
    """
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=root)
    return {"source_commit": commit, "source_dirty": bool(status)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--output", type=Path)
    modes.add_argument("--recount", type=Path, help="Verify token/byte counts from an archived exact context")
    parser.add_argument("--baseline", default="921bfe7", help="Revision for baseline instructions and tool names only; schemas use current registrations (see REBASE.md)")
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("output exists; preserve previous measurements and choose another filename")
    import tiktoken
    if importlib.metadata.version("tiktoken") != "0.12.0":
        parser.error("D1 requires tiktoken==0.12.0")
    encoding = tiktoken.get_encoding("o200k_base")
    if args.recount:
        archived = json.loads(args.recount.read_text(encoding="utf-8"))
        for case in archived["scenarios"]:
            text = case["serialized_context"]
            if sha(text) != case["sha256"]:
                raise ValueError(f"Context hash mismatch for {case['name']}")
            if len(text.encode("utf-8")) != case["utf8_bytes"]:
                raise ValueError(f"Context byte count mismatch for {case['name']}")
            if len(encoding.encode(text, disallowed_special=())) != case["tokens"]:
                raise ValueError(f"Context token count mismatch for {case['name']}")
        print("Verified all archived context hashes, byte counts and token counts.")
        return
    sys.path.insert(0, str(ROOT / "tools/mcp"))
    import morlet_mcp as server
    baseline = subprocess.check_output(["git", "rev-parse", args.baseline], cwd=ROOT, text=True, encoding="utf-8").strip()
    original = subprocess.check_output(["git", "show", f"{baseline}:tools/mcp/morlet_mcp.py"], cwd=ROOT, text=True, encoding="utf-8")
    instructions = next(ast.literal_eval(n.value) for n in ast.parse(original).body
                        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "INSTRUCTIONS" for t in n.targets))
    tools = asyncio.run(server.mcp.list_tools())
    original_names = {n.name for n in ast.parse(original).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "tool"
                              and isinstance(d.func.value, ast.Name) and d.func.value.id == "mcp" for d in n.decorator_list)}
    old = [t for t in tools if t.name in original_names]
    if {t.name for t in old} != original_names:
        raise RuntimeError("Baseline registration names differ from the current server")
    def definitions(items):
        return [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in sorted(items, key=lambda t: t.name)]

    started = time.perf_counter()
    index = asyncio.run(server.list_operations()).model_dump(mode="json")
    explanations = []
    for path in ("/input_schema", "/semantics"):
        request = {"name": "investigate_interval_v2", "path": path}
        result = asyncio.run(server.describe_operation(**request)).model_dump(mode="json")
        explanations.append({"operation": "describe_operation", "request": request, "response": result})
    elapsed = time.perf_counter() - started
    cases = [
        ("legacy_eager", 0, {"instructions": instructions, "tools": definitions(old)}),
        ("current_eager", 0, {"instructions": server.INSTRUCTIONS, "tools": definitions(tools)}),
        ("known_operation_selective_cli", 2, explanations),
        ("unknown_operation_selective_cli", 3, [{"operation": "list_operations", "request": {}, "response": index}] + explanations),
    ]
    scenarios = []
    for name, calls, payload in cases:
        text = serialize(payload)
        scenarios.append({"name": name, "discovery_invocations": calls,
                          "utf8_bytes": len(text.encode()), "tokens": len(encoding.encode(text, disallowed_special=())),
                          "sha256": sha(text), "serialized_context": text})
    sources = sorted((ROOT / "tools/mcp").glob("*.py")) + [Path(__file__), Path(__file__).with_name("PROTOCOL.md")]
    report = {"protocol": "D1", "classification": "development; deterministic context accounting, not agent performance",
              "timestamp_utc": datetime.now(timezone.utc).isoformat(), "baseline_commit": baseline,
              **source_state(ROOT),
              "baseline_definition_source": "current registrations filtered by baseline names; not historical schemas",
              "python": sys.version, "packages": {p: importlib.metadata.version(p) for p in ("tiktoken", "mcp", "pydantic")},
              "encoding": "o200k_base", "legacy_tools": len(old), "current_tools": len(tools),
              "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
              "discovery_seconds_single_observation": elapsed,
              "contract_hashes": {x["name"]: x["contract_hash"] for x in index["operations"]},
              "scenarios": scenarios,
              "limits": ["No model calls or behavioral token savings measured.",
                         "Selective CLI requires deferred discovery; eager MCP clients still load every schema.",
                         "No provider message overhead, hidden reasoning, or investigation result tokens counted."]}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{k: v for k, v in row.items() if k != "serialized_context"} for row in scenarios], indent=2))


if __name__ == "__main__":
    main()
