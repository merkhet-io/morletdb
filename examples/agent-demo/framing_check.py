"""How search_from_time frames its window: which framing around an onset gives the most catalogued events in
the top 10? Demo data
(development period 2025-03-11..14, CX PB01/PB02/PB06/PSGCX), every catalogued event with a clear onset at a
station as a query. Product engineering on development data, not a paper result. Run from this folder with
the demo loaded:

  docker compose run --rm -T -e DATABASE_URL=postgresql://agent_reader:agent-demo-only@db:5432/morlet \
    -v "$PWD/framing_check.py:/tmp/f.py:ro" -v "$PWD:/out" load python /tmp/f.py

Result on 2026-11-02 (203 queries, 52 events): share of the top 10 overlapping a catalogued event was 0.278
(onset-15s), 0.222 (onset-5s), 0.217 (onset-2s), 0.280 (onset+6s), 0.194 (max-energy) and 0.318 (onset-2s with
shifts=2; +0.040 [0.010, 0.070] over onset-15s, bootstrap over events)."""
import json, sys, statistics, time
sys.path.insert(0, "/app/tools/mcp"); sys.path.insert(0, "/app/examples/agent-demo")
import numpy as np
import morlet_mcp as m, seismo_mcp as sm
from obspy import UTCDateTime as T
C, W, RATE = "public.seismic", 4096, 100.0
recs = m.list_recordings(C, "{}", 500)["recordings"]
def rec_at(st, t):
    for r in recs:
        if r["metadata"]["station"] == st and T(r["start_utc"]) <= t < T(r["end_utc"]):
            return r
SPAN, EV = {}, {}
for st in sm.STATIONS:
    for e in sm.catalog_events(station=st)["events"]:
        SPAN.setdefault(st, []).append((T(e["p_arrival_utc"]), T(e["s_arrival_utc"]) + 20, e["event_id"]))
def rel(h, meta, own):
    t0 = T(h["start_utc"]); t1 = t0 + W / RATE
    ids = [i for a, b, i in SPAN.get(meta["station"], []) if t0 < b and t1 > a]
    return bool(ids), bool([i for i in ids if i != own])
# queries: catalogued events with a clear onset within 10 s of the predicted P
queries = []
for st in sm.STATIONS:
    for e in sm.catalog_events(station=st)["events"]:
        p = T(e["p_arrival_utc"]); r = rec_at(st, p - 60)
        if not r or not rec_at(st, p + 60) or rec_at(st, p + 60)["recording_id"] != r["recording_id"]:
            continue
        t0 = T(r["start_utc"]); a = int((p - 45 - t0) * RATE)
        o = m.find_onsets(C, r["recording_id"], a, a + 7000, 2.0, 0.0, 1.0, 30.0, 6.0)["onsets"]
        o = [x for x in o if abs(T(x["trigger_utc"]) - p) <= 10]
        if o:
            queries.append((st, e["event_id"], r["recording_id"], o[0]["trigger_sample"], t0))
print("queries", len(queries), flush=True)
def strongest(rid, onset, lo_s, hi_s, step_s=2.0):
    x = m.read(m.connect(), C, rid, onset + int(lo_s * RATE), onset + int(hi_s * RATE) + W)
    y = m.bandpass(x, RATE, 2.0, 0.0)
    best, arg = -1, 0
    for k in range(0, int((hi_s - lo_s) / step_s) + 1):
        s0 = int(k * step_s * RATE); e2 = float(np.mean(y[s0:s0 + W] ** 2))
        if e2 > best: best, arg = e2, s0
    return onset + int(lo_s * RATE) + arg
rules = {"onset-15s": lambda rid, o: o - 1500, "onset-5s": lambda rid, o: o - 500, "onset-2s": lambda rid, o: o - 200,
         "onset+6s": lambda rid, o: o + 600, "max-energy(-20..+20s)": lambda rid, o: strongest(rid, o, -20, 20)}
out = {k: {"any": [], "other": []} for k in list(rules) + ["onset-2s,shifts=2"]}
t_start = time.time()
for qi, (st, ev, rid, onset, t0) in enumerate(queries):
    for name, f in list(rules.items()) + [("onset-2s,shifts=2", rules["onset-2s"])]:
        a = f(rid, onset)
        r = m.search_similar(C, rid, a, a + W, "{}", 10, 2 if "shifts" in name else 0)
        flags = [rel(h, r["recordings"][h["recording_id"]], ev) for h in r["hits"]]
        out[name]["any"].append(sum(f_[0] for f_ in flags) / 10); out[name]["other"].append(sum(f_[1] for f_ in flags) / 10)
    if qi % 10 == 0: print(qi, round(time.time() - t_start), flush=True)
print(json.dumps({k: {"n": len(v["any"]), "any": round(statistics.mean(v["any"]), 3), "other_events": round(statistics.mean(v["other"]), 3)} for k, v in out.items()}, indent=1))
json.dump({"queries": [(q[0], q[1], q[2], q[3]) for q in queries], "per_query": out}, open("/out/framing_eval.json", "w"))
