"""Calls the MorletDB MCP server over stdio (as Claude would) and stores the tool results for the figures."""
import asyncio, json, os, sys, statistics as st
from pathlib import Path
from mcp.client import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

OUT = Path("/spike/results")
rows = json.loads((OUT / "t12_conf.json").read_text())["rows"]
runs = {}
for r in rows:
    e = runs.setdefault(r["run"], dict(m=r["m"], op=r["op"], label=r["label"], w=[]))
    if r["t2_BL"] is not None:
        e["w"].append((r["t2_BL"], r["ax"], r["s"]))
for e in runs.values():
    e["score"] = st.median(x[0] for x in e["w"])
scores = [dict(run=k, m=e["m"], op=e["op"], label=e["label"], score=e["score"], period="_".join(k.split("_")[1:3]))
          for k, e in runs.items()]
bad = max((k for k in runs if runs[k]["label"] == "bad"), key=lambda k: runs[k]["score"])
B = runs[bad]
_, ax, s = max(B["w"])
# good comparison run from the same half-year as the bad run (A1: avoids drift between half-years)
bad_period = "_".join(bad.split("_")[1:3])
peers = sorted((k for k in runs if runs[k]["label"] == "good" and runs[k]["m"] == B["m"] and runs[k]["op"] == B["op"]
                and "_".join(k.split("_")[1:3]) == bad_period), key=lambda k: runs[k]["score"])
good = peers[len(peers) // 2]
C = "public.bosch"


async def main():
    params = StdioServerParameters(command=sys.executable, args=["/app/morlet_mcp.py"],
                                   env={"DATABASE_URL": os.environ["DATABASE_URL"], "PATH": os.environ.get("PATH", ""), "PYTHONPATH": "/app"})
    res = dict(bad=bad, good=good, axis=ax, start=s, op=B["op"], machine=B["m"], scores=scores, calls=[])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as ses:
            await ses.initialize()
            res["tools"] = [t.name for t in (await ses.list_tools()).tools]

            async def call(name, **a):
                r = await ses.call_tool(name, a)
                txt = r.content[0].text
                try:
                    val = json.loads(txt)
                except Exception:
                    val = txt
                res["calls"].append(dict(tool=name, args=a, error=bool(r.is_error), result=val))
                return val
            await call("list_collections")
            lr = await call("list_recordings", collection=C, metadata_filter=json.dumps({"run": bad}), include_metadata=True)
            for tag, run in (("bad", bad), ("good", good)):
                rid = f"{run}_{ax}"
                n = (await call("list_recordings", collection=C, metadata_filter=json.dumps({"run": run, "axis": ax})))
                res[tag + "_list"] = n
                length = n["recordings"][0].get("samples") or n["recordings"][0].get("length_samples")
                res[tag + "_len"] = length
                await call("read_interval", collection=C, recording_id=rid, start_sample=0, end_sample=length, max_points=400)
                await call("describe_interval", collection=C, recording_id=rid, start_sample=s, end_sample=s + 4096)
                await call("read_interval", collection=C, recording_id=rid, start_sample=s, end_sample=s + 4096, max_points=4096)
            await call("search_similar", collection=C, recording_id=f"{bad}_{ax}", start_sample=s, end_sample=s + 4096,
                       metadata_filter=json.dumps({"machine": B["m"], "op": B["op"], "axis": ax}), top_k=8)
            await call("compare_intervals", collection=C, recording_id=f"{bad}_{ax}", start_sample=s, end_sample=s + 4096,
                       reference_recording_id=f"{good}_{ax}", reference_start_sample=s, reference_end_sample=s + 4096)
    (OUT / "mcp_demo.json").write_text(json.dumps(res))
    print("bad", bad, "good", good, "axis", ax, "start", s, "tools", len(res["tools"]))
    for c in res["calls"]:
        print(c["tool"], "ERROR" if c["error"] else "ok", str(c["result"])[:300])

asyncio.run(main())
