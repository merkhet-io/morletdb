#!/usr/bin/env python3
"""IU4 runner (PROTOCOL.md, Implementation): causal ingest, states and decisions, run by run, tool by tool.

  iu4.py init                                  create public.dk (drops an existing one)
  iu4.py run MACHINE NPZ_DIR OUT_DIR [MODEL]   store and describe every run of the machine's tools in order; with MODEL
                                               (C's exported model) also write decisions with evidence; label each
                                               tool's life when its last run is stored
MORLET_DSN. Uses tools/mcp/decision.py (decision_states, segments_at). Never prints signal values.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools" / "mcp"))
sys.path.insert(0, str(HERE))
import decision  # noqa: E402
from features import SIGNALS, feature_dict, predict, evidence  # noqa: E402

COLL = "public.dk"
DSN = os.environ.get("MORLET_DSN", "")
RATES = {"force_sensor_x": 25000.0, "force_sensor_y": 25000.0, "force_sensor_z": 25000.0}
UNITS = {"force_sensor_x": "N", "force_sensor_y": "N", "force_sensor_z": "N", "torque_spindle": "Nm",
         "torque_axis_x": "Nm", "torque_axis_y": "Nm", "torque_axis_z": "Nm",
         "position_control_deviation_axis_x": "mm", "position_control_deviation_axis_y": "mm"}
WINDOWS = {"force": (4096, 2048), "machine": (512, 256)}      # B: product defaults / product minimum
N_BASELINE, QUERIES, LAG = 10, 8, 0.075
WORN_VB = 100.0
TOOLS = {1: (1, 2, 3), 2: (4, 5, 6), 3: (7, 8, 9)}
NAME = re.compile(r"^M(\d+)T(\d+)R(\d+)\.npz$")


def rid(machine, tool, run, sig):
    return f"m{machine}t{tool}r{run:04d}-{sig}"


def window(sig):
    return WINDOWS["force" if sig.startswith("force") else "machine"]


def connect():
    return psycopg.connect(DSN, autocommit=True)


def init(c):
    c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
    names = [r[0] for r in c.execute("SELECT quote_ident(tablename) FROM pg_tables WHERE schemaname='public' "
                                     "AND (tablename='dk' OR tablename LIKE 'dk\\_%')")]
    if names:
        c.execute(f"DROP TABLE {', '.join(names)} CASCADE")
    c.execute("SELECT morlet_create_stream_collection('public','dk')")
    c.execute("SELECT morlet_enable_context('dk')")
    c.execute("SELECT morlet_enable_envelopes('dk'::regclass)")
    return c.execute("SELECT morlet_version()").fetchone()[0], c.execute("SELECT morlet_value_rules('dk')").fetchone()[0]


def store_run(c, machine, tool, run, arrays, gallery, fields=None):
    """The run's 9 signals as recordings, their context, one segment each, values; envelopes for the gallery."""
    for sig in SIGNALS:
        x = np.ascontiguousarray(arrays[sig], dtype="<f8")
        r = rid(machine, tool, run, sig)
        rate = RATES.get(sig, 500.0)
        meta = {"machine": machine, "tool": tool, "run": run, "signal": sig, "baseline": run <= N_BASELINE,
                "source_field": (fields or {}).get(sig, sig)}
        with c.transaction():
            c.execute("SELECT morlet_start_recording(%s,%s,%s,%s,%s,%s::jsonb,512,256,'zscore','none')",
                      (COLL, r, rate, sig, UNITS[sig], json.dumps(meta)))
            c.execute("SELECT morlet_append_chunks_raw(%s,%s,0,%s,16384)", (COLL, r, x.tobytes()))
            n = x.size
            ctx = [{"recording_id": r, "key": k, "value": v, "start_sample": 0, "end_sample": n}
                   for k, v in (("process", "denkena2023"), ("machine", machine), ("tool", tool), ("run", run))]
            c.execute("SELECT morlet_add_context(%s,%s,%s)", (COLL, f"load-{r}", json.dumps(ctx)))
            c.execute("SELECT morlet_build_context_segments(%s,%s,ARRAY['process','machine','tool'],ARRAY['run'])",
                      (COLL, r))
            c.execute("SELECT morlet_build_context_values(%s,%s)", (COLL, r))
        if gallery:
            w, s = window(sig)
            while c.execute("SELECT morlet_build_envelopes(%s,%s,%s,%s,10000)", (COLL, r, w, s)).fetchone()[0]:
                pass


def run_states(c, machine, tool, run, base):
    segs = decision.segments_at(c, COLL, [(rid(machine, tool, run, sig), 0) for sig in SIGNALS])
    states = decision.decision_states(c, COLL, segs, match_keys=["process", "machine", "tool"], min_history=3,
                                      neighbors=3, baselines={s: base[sig] for s, sig in zip(segs, SIGNALS)})
    return dict(zip(SIGNALS, states))


def query_starts(n, w, s, k=QUERIES):
    starts = list(range(0, n - w + 1, s))
    if not starts:
        return []
    idx = sorted(set(int(round(i)) for i in np.linspace(0, len(starts) - 1, k)))
    return [starts[i] for i in idx]


def shape_search(c, machine, tool, run, lengths):
    """Arm B (deviation 1): per signal, nearest-hit distances of up to 8 query windows against the tool's runs 1-10."""
    out = {}
    for sig in SIGNALS:
        w, s = window(sig)
        flt = json.dumps({"machine": machine, "tool": tool, "signal": sig, "baseline": True})
        d = []
        for q in query_starts(lengths[sig], w, s):
            h = c.execute("SELECT recording_id,start_sample,distance FROM morlet_search_envelope_interval"
                          "(%s,%s,%s,%s,%s::jsonb,1,%s)", (COLL, rid(machine, tool, run, sig), q, q + w, flt, LAG)).fetchall()
            d.append(h[0][2] if h else None)
        out[sig] = d
    return out


def label_life(c, machine, tool, wear):
    """After the tool's last run: worn (VB >= 100 um) or sharp on every recording of every run."""
    with c.transaction():
        for run, vb in sorted(wear.items()):
            lab = "worn" if vb >= WORN_VB else "sharp"
            for sig in SIGNALS:
                r = rid(machine, tool, run, sig)
                end = c.execute("SELECT received_samples FROM morlet_stream_status(%s,%s)", (COLL, r)).fetchone()[0]
                c.execute("SELECT morlet_add_label(%s,%s,0,%s,%s,%s)",
                          (COLL, r, end, lab, f"IU4: VB {vb:g} um, labelled after the tool's life ended"))


def runs_of(npz_dir, machine, tool):
    out = []
    for p in npz_dir.glob(f"M{machine}T{tool}R*.npz"):
        m = NAME.match(p.name)
        if m:
            out.append((int(m.group(3)), p))
    return sorted(out)


def run(machine, npz_dir, out, model_path=None):
    out.mkdir(parents=True, exist_ok=True)
    model = json.loads(Path(model_path).read_text()) if model_path else None
    with connect() as c:
        for tool in TOOLS[machine]:
            t0 = time.time()
            states_f = (out / f"states_m{machine}t{tool}.jsonl").open("w")
            dec_f = (out / f"decisions_m{machine}t{tool}.jsonl").open("w") if model else None
            wear, base = {}, None
            runs = runs_of(npz_dir, machine, tool)
            for run_no, p in runs:
                z = np.load(p)
                arrays = {sig: z[sig] for sig in SIGNALS}
                labels = json.loads(str(z["labels"]))
                wear[run_no] = labels["wear"]
                ts = time.perf_counter()
                fields = json.loads(str(z["fields"])) if "fields" in z.files else None
                store_run(c, machine, tool, run_no, arrays, gallery=run_no <= N_BASELINE, fields=fields)
                t_store = time.perf_counter() - ts
                if run_no <= N_BASELINE:
                    continue
                if base is None:
                    base = {sig: [decision.segment_at(c, COLL, rid(machine, tool, k, sig), 0)
                                  for k in range(1, N_BASELINE + 1)] for sig in SIGNALS}
                t = time.perf_counter()
                states = run_states(c, machine, tool, run_no, base)
                t_state = time.perf_counter() - t
                t = time.perf_counter()
                b = shape_search(c, machine, tool, run_no, {sig: arrays[sig].size for sig in SIGNALS})
                t_search = time.perf_counter() - t
                states_f.write(json.dumps({"machine": machine, "tool": tool, "run": run_no, "vb": labels["wear"],
                                           "states": states, "shape": b,
                                           "seconds": {"store": t_store, "states": t_state, "shape": t_search}}) + "\n")
                states_f.flush()
                if model:
                    for arm in ("full", "controller"):
                        fd = feature_dict(states, arm)
                        prob, contrib = predict(model[arm], fd)
                        top = max(contrib, key=lambda k: abs(contrib[k]))
                        top_sig = top.split("|")[0]
                        ex = decision.decision_states(c, COLL, [states[top_sig]["segment"]["segment_id"]],
                                                      match_keys=["process", "machine", "tool"], min_history=3,
                                                      neighbors=0, examples=3, example_match_keys=["process"])[0]
                        dec_f.write(json.dumps({"machine": machine, "tool": tool, "run": run_no, "arm": arm,
                                                "probability": prob, "evidence": evidence(contrib, states, ex)}) + "\n")
                    dec_f.flush()
                if run_no % 50 == 0:
                    print(f"m{machine}t{tool} run {run_no}/{len(runs)} {time.time() - t0:.0f}s", flush=True)
            label_life(c, machine, tool, wear)
            states_f.close()
            if dec_f:
                dec_f.close()
            print(f"m{machine}t{tool} done: {len(runs)} runs in {time.time() - t0:.0f}s", flush=True)


def main():
    mode = sys.argv[1]
    if mode == "init":
        with connect() as c:
            print("pg_morlet, value rules:", init(c))
    elif mode == "run":
        run(int(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5] if len(sys.argv) > 5 else None)


if __name__ == "__main__":
    main()
