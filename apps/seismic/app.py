"""Local seismic investigation UI. Run: streamlit run apps/seismic/app.py

Binds to localhost (see .streamlit/config.toml). Research tool: similarity is a
lead for analyst review, never an event label, alert or classification.
"""
from datetime import datetime, timezone
import threading
import time

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from morlet_seismic import catalog, cli, investigate, retrieval
from morlet_seismic.ingest import init_db, iso_ns, run
from morlet_seismic.sources import ReplayControl, ReplaySource, SeedLinkSource

QUERY, MATCH = "#2a78d6", "#eb6834"   # categorical slots 1 and 2 (validated reference palette)
st.set_page_config(page_title="MorletDB seismic investigation", layout="wide")


# -- shared resources ---------------------------------------------------------
@st.cache_resource
def project():
    return investigate.load(cli.DEFAULT_DEFINITION)


@st.cache_resource
def worker_slot():
    return {"thread": None, "control": None, "source": None, "ingestor": None, "error": None,
            "mode": None, "started_wall": None}


def conn():
    if "conn" not in st.session_state or st.session_state.conn.closed:
        st.session_state.conn = investigate.connect(st.session_state.get("dsn"))
    c = st.session_state.conn
    c.rollback()  # Always read the latest committed state.
    return c


@st.cache_resource(show_spinner="Reconstructing the reference gallery from MorletDB samples...")
def gallery_for(dsn, dataset_id, stream, background_every_s, _version):
    definition, _, _ = project()
    rep, det = definition["representation"], definition["detector"]
    with investigate.connect(dsn) as c:
        segs = investigate.segments(c, dataset_id, stream)
        trigs = investigate.triggers(c, dataset_id, det["version"], stream)
        windows, _ = investigate.cap_gallery(investigate.gallery_windows(
            segs, trigs, rep, det["query_pre_s"], background_every_s=background_every_s))
        if not windows:
            return None
        spec = retrieval.window_spec(rep, stream.split(".")[-1], rep["units"], segs[0]["rate"])
        return retrieval.build_gallery(investigate.client(c), spec, windows)


def start_worker(mode, dataset_id, **options):
    slot = worker_slot()
    definition, manifest, base = project()

    def target():
        try:
            with investigate.connect(st.session_state.get("dsn")) as c:
                init_db(c)
                ingestor, detector = cli.build_pipeline(c, definition, manifest, dataset_id=dataset_id,
                                                        mode=mode)
                slot["ingestor"] = ingestor
                if mode == "replay":
                    run(slot["source"], ingestor, detector=detector)
                else:
                    for chunk in slot["source"].chunks():
                        ingestor.handle(chunk)
                        ingestor.flush()
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            slot["error"] = f"{type(exc).__name__}: {exc}"

    if mode == "replay":
        control = ReplayControl(options["speed"])
        slot["source"] = ReplaySource(manifest, base, streams=options["streams"],
                                      periods=options["periods"], control=control,
                                      chunk_samples=definition["ingest"]["chunk_samples"])
    else:
        slot["source"] = SeedLinkSource(cli.LIVE_SERVER, options["streams"], enabled=True)
        control = slot["source"].control
    slot.update(control=control, error=None, mode=mode, started_wall=time.time(), ingestor=None)
    slot["thread"] = threading.Thread(target=target, daemon=True, name=f"{mode}-ingest")
    slot["thread"].start()


# -- reading helpers ------------------------------------------------------------
def utc(ns):
    return pd.to_datetime(ns, unit="ns", utc=True)


def read_samples(c, recording_id, start, end):
    client, values = investigate.client(c), []
    for lo in range(start, end, 32768):
        values.extend(client.read_interval(recording_id, lo, min(end, lo + 32768))["samples"])
    return np.asarray(values)


def waveform_frame(segment, start, end, samples, label):
    times = utc(segment["start_ns"] + np.round(np.arange(start, end) * 1e9 / segment["rate"]).astype("int64"))
    return pd.DataFrame({"sample time (UTC)": times, "counts (demeaned)": samples - samples.mean(),
                         "series": label, "sample": np.arange(start, end)})


def spectrum_frame(samples, rate, label):
    x = (samples - samples.mean()) * np.hanning(len(samples))
    amplitude = np.abs(np.fft.rfft(x)) / len(x)
    freqs = np.fft.rfftfreq(len(x), 1 / rate)
    keep = freqs >= 0.5
    return pd.DataFrame({"frequency (Hz)": freqs[keep], "amplitude (counts)": amplitude[keep],
                         "series": label})


def line(frame, x, y, title, height=220, color_domain=("query",), color_range=(QUERY,)):
    return (alt.Chart(frame, title=title).mark_line(strokeWidth=1.5)
            .encode(x=alt.X(x, title=x), y=alt.Y(y, title=y),
                    color=alt.Color("series", scale=alt.Scale(domain=list(color_domain),
                                                              range=list(color_range)),
                                    legend=alt.Legend(orient="top", title=None)),
                    tooltip=[x, alt.Tooltip(y, format=".4g"), "series"])
            .properties(height=height).interactive(bind_y=False))


def spectrogram(samples, rate, start_ns):
    import matplotlib.pyplot as plt
    from scipy.signal import spectrogram as spec
    f, t, power = spec(samples - samples.mean(), fs=rate, nperseg=256, noverlap=192)
    fig, ax = plt.subplots(figsize=(10, 2.6))
    times = utc(start_ns + (t * 1e9).astype("int64"))
    ax.pcolormesh(times, f, 10 * np.log10(power + 1e-12), shading="auto", cmap="Blues")
    ax.set_ylabel("frequency (Hz)")
    ax.set_xlabel("sample time (UTC)")
    ax.set_ylim(0, rate / 2)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    return fig


# -- sidebar ----------------------------------------------------------------------
definition, manifest, base = project()
rep, det = definition["representation"], definition["detector"]
st.sidebar.title("MorletDB · seismic")
st.session_state.dsn = st.sidebar.text_input("PostgreSQL DSN", investigate.DEFAULT_DSN)
try:
    c = conn()
    init_db(c)
except Exception as exc:  # noqa: BLE001
    st.error(f"Database unavailable: {exc}. Start it with `docker compose -f apps/seismic/compose.yaml up -d --wait`.")
    st.stop()

datasets = [r[0] for r in c.execute("SELECT DISTINCT dataset FROM seismic.segments ORDER BY 1").fetchall()]
datasets = datasets or [definition["id"]]
dataset_id = st.sidebar.selectbox("Dataset", datasets,
                                  index=datasets.index(definition["id"]) if definition["id"] in datasets else 0)
streams = investigate.stream_metadata(manifest)
stream = st.sidebar.selectbox("Station / channel", sorted(streams))

slot = worker_slot()
running = slot["thread"] is not None and slot["thread"].is_alive()
mode_label = (f"● LIVE · {cli.LIVE_SERVER}" if running and slot["mode"] == "live"
              else "▶ REPLAY · historical samples" if running else "■ idle · showing stored data")
st.sidebar.markdown(f"**Input:** {mode_label}")

with st.sidebar.expander("Historical replay", expanded=not running):
    periods = st.multiselect("Periods", [p["name"] for p in definition["periods"]],
                             default=[p["name"] for p in definition["periods"]])
    speed = st.number_input("Speed (× real time; 0 = unpaced)", 0.0, 100000.0, 600.0, step=100.0)
    cols = st.columns(3)
    if cols[0].button("Start", disabled=running):
        start_worker("replay", definition["id"], speed=speed, streams=[stream], periods=periods)
        st.rerun()
    if running and slot["mode"] == "replay":
        if slot["control"].paused:
            if cols[1].button("Resume"):
                slot["control"].resume()
        elif cols[1].button("Pause"):
            slot["control"].pause()
        if cols[2].button("Stop"):
            slot["control"].stop()
    st.caption("Replay re-emits cached miniSEED through the same ingestion as live input. Already "
               "committed samples are verified as duplicates, not re-appended.")

with st.sidebar.expander("Live SeedLink (opt-in)"):
    st.caption(f"Connects one session to the public GEOFON SeedLink server {cli.LIVE_SERVER} "
               f"and ingests into dataset `{cli.LIVE_DATASET}`. Backoff 10 s → 5 min on disconnect.")
    allow = st.checkbox("I want to open a live network connection")
    if st.button("Start live", disabled=running or not allow):
        start_worker("live", cli.LIVE_DATASET, streams=[stream])
        st.rerun()
    if running and slot["mode"] == "live" and st.button("Stop live"):
        slot["source"].stop()

if slot["error"]:
    st.sidebar.error(f"Ingestion stopped: {slot['error']}")
if running:
    status = slot["source"].status()
    sample_time = status.get("replay_time_ns")
    st.sidebar.markdown(
        f"- sample time: `{iso_ns(sample_time)[:22] if sample_time else '–'}`\n"
        f"- processing clock (local): `{datetime.now(timezone.utc).isoformat()[:19]}Z`\n"
        f"- chunks emitted: {status.get('emitted_chunks', '–')}; paused: {status.get('paused', False)}")
    if slot["mode"] == "live":
        st.sidebar.json(status)
    if slot["ingestor"] is not None:
        st.sidebar.json({k: v for k, v in slot["ingestor"].stats.items()})
    st.sidebar.button("Refresh")

# -- data -------------------------------------------------------------------------
segs = investigate.segments(c, dataset_id, stream)
trigs = investigate.triggers(c, dataset_id, det["version"], stream)
arrivals = [a for a in catalog.predicted_arrivals(manifest, definition["association"]) if a["stream"] == stream]
associations = catalog.associate(trigs, arrivals)
by_id = {s["recording_id"]: s for s in segs}


def overview_tab():
    progress = c.execute("SELECT stream,mode,last_committed_end_ns,committed_samples,duplicate_samples,gaps,"
                         "conflicts,errors,updated_wall,last_chunk_emitted_wall FROM seismic.progress "
                         "WHERE dataset=%s ORDER BY stream", (dataset_id,)).fetchall()
    now = time.time()
    st.subheader("Ingestion progress")
    st.dataframe(pd.DataFrame([{
        "stream": p[0], "mode": p[1],
        "last committed sample (UTC sample time)": iso_ns(p[2])[:23] if p[2] else "–",
        "sample freshness vs now": f"{(now - p[2] / 1e9) / 3600:.1f} h" if p[2] else "–",
        "committed samples": p[3], "verified duplicate samples": p[4], "gaps": p[5],
        "overlap conflicts": p[6], "rejected chunks": p[7],
        "last commit (local processing clock)": p[8].astimezone(timezone.utc).isoformat()[:19] + "Z"}
        for p in progress]), hide_index=True, width="stretch")
    st.caption("Sample time comes from the miniSEED headers. Processing time is when this machine "
               "committed it. In replay, freshness measures the historical age of the data, not a delay.")
    if not segs:
        st.info("No recordings yet for this stream. Start a replay from the sidebar or run "
                "`python -m morlet_seismic replay`.")
        return
    seg_frame = pd.DataFrame([{"row": f"recordings ({s['split']})", "start": utc(s["start_ns"]), "end": utc(s["end_ns"]),
                               "recording": s["recording_id"], "split": s["split"],
                               "opened because": s["opened_reason"], "samples": s["committed"]} for s in segs])
    trig_frame = pd.DataFrame([{"row": "detector triggers", "time": utc(t["on_ns"]), "peak STA/LTA": t["peak_ratio"],
                                "duration s": (t["off_ns"] - t["on_ns"]) / 1e9,
                                "catalog association": (associations[(t["recording_id"], t["on_sample"])] or [{}])[0].get("event_id", "none")}
                               for t in trigs])
    cat_frame = pd.DataFrame([{"row": "catalog predicted P (reference only)", "time": utc(a["p_ns"]),
                               "event": a["event_id"], "magnitude": a["magnitude"], "depth km": a["depth_km"],
                               "distance deg": round(a["distance_deg"], 2)} for a in arrivals])
    split_choice = st.radio("Time period", [p["name"] for p in definition["periods"]], horizontal=True)
    period = next(p for p in definition["periods"] if p["name"] == split_choice)
    lo, hi = pd.Timestamp(period["start"]), pd.Timestamp(period["end"])
    domain = alt.Scale(domain=[lo, hi])
    bars = alt.Chart(seg_frame[(seg_frame.end > lo) & (seg_frame.start < hi)]).mark_bar(height=14, color="#7f8c8d").encode(
        x=alt.X("start:T", scale=domain, title="sample time (UTC)"), x2="end:T", y=alt.Y("row:N", title=None),
        tooltip=["recording", "split", "opened because", "samples", "start:T", "end:T"])
    layers = [bars]
    if not trig_frame.empty:
        layers.append(alt.Chart(trig_frame[(trig_frame.time > lo) & (trig_frame.time < hi)]).mark_tick(
            color=QUERY, thickness=2, size=14).encode(
            x=alt.X("time:T", scale=domain), y="row:N",
            tooltip=["time:T", alt.Tooltip("peak STA/LTA", format=".1f"), "duration s", "catalog association"]))
    if not cat_frame.empty:
        layers.append(alt.Chart(cat_frame[(cat_frame.time > lo) & (cat_frame.time < hi)]).mark_tick(
            color=MATCH, thickness=2, size=14).encode(
            x=alt.X("time:T", scale=domain), y="row:N",
            tooltip=["time:T", "event", "magnitude", "depth km", "distance deg"]))
    st.subheader("Timeline")
    st.altair_chart(alt.layer(*layers).properties(height=150).interactive(bind_y=False), use_container_width=True)
    st.caption("Gray bars: committed recordings (gaps are white space; nothing is filled). Blue ticks: "
               "STA/LTA triggers. Orange ticks: USGS catalog events at their IASP91-predicted P arrival, "
               "a review overlay that never drives the detector.")
    events = c.execute("SELECT kind,sample_ns,recording_id,detail,wall FROM seismic.events WHERE dataset=%s AND stream=%s"
                       " AND kind NOT IN ('segment_opened') ORDER BY id DESC LIMIT 200", (dataset_id, stream)).fetchall()
    st.subheader("Discontinuities and ingestion errors")
    st.dataframe(pd.DataFrame([{"kind": e[0], "sample time (UTC)": iso_ns(e[1])[:23] if e[1] else "–",
                                "recording": e[2], "detail": e[3],
                                "logged (processing clock)": e[4].astimezone(timezone.utc).isoformat()[:19] + "Z"}
                               for e in events]), hide_index=True, width="stretch", height=240)


def investigate_tab():
    left, right = st.columns([1, 2])
    with left:
        st.subheader("Candidate intervals")
        split_filter = st.selectbox("Query split", ["heldout", "reference", "live"], index=0)
        rows = []
        for t in trigs:
            if t["split"] != split_filter:
                continue
            a = associations[(t["recording_id"], t["on_sample"])]
            rows.append({"onset (UTC)": iso_ns(t["on_ns"])[:22], "peak STA/LTA": round(t["peak_ratio"], 1),
                         "duration s": round((t["off_ns"] - t["on_ns"]) / 1e9, 1),
                         "catalog reference": f"{a[0]['event_id']} M{a[0]['magnitude']}" if a else "—",
                         "_key": (t["recording_id"], t["on_sample"])})
        table = pd.DataFrame(rows)
        if table.empty:
            st.info("No triggers in this split for this stream.")
            return
        only_cat = st.checkbox("Only catalog-associated triggers", value=False)
        if only_cat:
            table = table[table["catalog reference"] != "—"]
        picked = st.dataframe(table.drop(columns="_key"), hide_index=True, width="stretch",
                              on_select="rerun", selection_mode="single-row", height=420)
        selected = picked.selection.rows[0] if picked.selection.rows else 0
        key = table.iloc[selected]["_key"]
    trigger = next(t for t in trigs if (t["recording_id"], t["on_sample"]) == key)
    segment = by_id[trigger["recording_id"]]
    qstart = investigate.query_start(trigger, segment, rep, det["query_pre_s"])
    with right:
        st.subheader("Selected query interval")
        if qstart is None:
            st.warning("This trigger is too close to a recording boundary for a full 2048-sample window; "
                       "no query is formed (windows never cross gaps).")
            return
        qend = qstart + rep["window_samples"]
        ctx_lo = max(0, qstart - 30 * int(segment["rate"]))
        ctx_hi = min(segment["committed"], qend + 30 * int(segment["rate"]))
        context = read_samples(c, segment["recording_id"], ctx_lo, ctx_hi)
        query_samples = context[qstart - ctx_lo:qend - ctx_lo]
        st.markdown(f"`{segment['recording_id']}` samples **[{qstart}, {qend})** = "
                    f"{iso_ns(segment['start_ns'] + round(qstart * 1e9 / segment['rate']))[:22]} → "
                    f"{iso_ns(segment['start_ns'] + round(qend * 1e9 / segment['rate']))[:22]} "
                    f"({rep['window_samples'] / segment['rate']:.2f} s at {segment['rate']:g} Hz)")
        frame = waveform_frame(segment, ctx_lo, ctx_hi, context, "query")
        frame["series"] = np.where((frame["sample"] >= qstart) & (frame["sample"] < qend), "query", "context")
        st.altair_chart(line(frame, "sample time (UTC)", "counts (demeaned)", "Raw waveform (±30 s context)",
                             color_domain=("context", "query"), color_range=("#b4b2a9", QUERY)),
                        use_container_width=True)
        st.pyplot(spectrogram(context, segment["rate"], segment["start_ns"] + round(ctx_lo * 1e9 / segment["rate"])))
        assoc = associations[key]
        with st.expander("Catalog reference annotation (separate from retrieval)", expanded=bool(assoc)):
            if assoc:
                st.dataframe(pd.DataFrame([{"event": a["event_id"], "M": a["magnitude"], "type": a["magnitude_type"],
                                            "depth km": a["depth_km"], "distance deg": round(a["distance_deg"], 2),
                                            "onset − predicted P (s)": round(a["p_residual_s"], 2)} for a in assoc]),
                             hide_index=True)
                st.caption("Association: onset inside [P−5 s, S+20 s] of an IASP91 prediction. Origin time is not "
                           "arrival time; location and model errors apply.")
            else:
                st.write("No cataloged event predicts an arrival here. Unmatched is not the same as false: "
                         "small local earthquakes are often uncataloged.")

    st.subheader("Historical matches (same station/channel, reference split)")
    mcols = st.columns(5)
    mode = mcols[0].selectbox("Retrieval mode", list(retrieval.MODES), format_func=lambda m: m)
    top_k = mcols[1].number_input("top-k", 1, 20, 5)
    candidate_budget = mcols[2].number_input("candidate budget (approx.)", 1, 4096, 64)
    posting_budget = mcols[3].number_input("posting budget", 1, 1_000_000, 1_000_000)
    background = mcols[4].number_input("gallery background grid (s)", 60, 3600, 300)
    st.caption(retrieval.MODES[mode] + ". " + " ".join(retrieval.UNAVAILABLE.values()))
    meta = retrieval.stream_filter(dataset_id, stream)
    client = investigate.client(c)
    if mode == "pooled-pg":
        result = retrieval.pooled_pg(client, segment["recording_id"], qstart, qend, meta, top_k=int(top_k))
        gallery = None
    else:
        try:
            gallery = gallery_for(st.session_state.dsn, dataset_id, stream, float(background),
                                  len([t for t in trigs if t["split"] == "reference"]))
        except FileNotFoundError as exc:
            st.error(f"Temporal modes unavailable: {exc}")
            return
        if gallery is None:
            st.warning("No reference windows for this stream: gallery modes unavailable.")
            return
        query = retrieval.query_window(client, gallery.spec, segment["recording_id"], qstart)
        if mode == "pooled-gallery":
            result = retrieval.pooled_gallery(gallery, query, top_k=int(top_k))
        else:
            result = retrieval.temporal(gallery, query, approximate=mode == "temporal-approx", top_k=int(top_k),
                                        candidate_budget=int(candidate_budget), posting_budget=int(posting_budget))
            if mode == "temporal-approx" and result["status"] == "ok":
                oracle = retrieval.temporal(gallery, query, top_k=int(top_k))
                result["recall_vs_exhaustive"] = retrieval.recall(result["matches"], oracle["matches"])
    stats = {k: result.get(k) for k in ("mode", "status", "exhaustive", "eligible", "scored", "posting_visits",
                                         "candidate_budget", "posting_budget", "recall_vs_exhaustive")}
    stats["query_ms"] = None if result.get("query_s") is None else round(result["query_s"] * 1e3, 1)
    st.json({k: v for k, v in stats.items() if v is not None})
    if gallery is not None:
        st.caption(f"In-memory gallery (not a PostgreSQL index): {gallery.build['windows']} windows "
                   f"{gallery.composition}, spec {gallery.build['spec_id'][:12]}…, generation "
                   f"{gallery.build['generation_id'][:12]}…, rebuilt from committed samples in "
                   f"{gallery.build['read_s'] + gallery.build['extract_s']:.1f} s.")
    if result["status"] != "ok":
        st.error(result.get("message", "retrieval budget exceeded; no partial top-k returned"))
    elif not result["matches"]:
        st.info("No compatible matches. The list stays empty; no label is inferred.")
    ref_arrivals = arrivals
    for rank, m in enumerate(result["matches"], 1):
        mseg = by_id.get(m["recording_id"]) or investigate.segments(c, dataset_id, stream)[0]
        samples = read_samples(c, m["recording_id"], m["start"], m["end"])
        start_ns = mseg["start_ns"] + round(m["start"] * 1e9 / mseg["rate"])
        end_ns = mseg["start_ns"] + round(m["end"] * 1e9 / mseg["rate"])
        refs = [a for a in ref_arrivals if start_ns < a["window_ns"][1] and a["window_ns"][0] < end_ns]
        ref_text = ", ".join(f"{a['event_id']} M{a['magnitude']}" for a in refs) or "none"
        with st.expander(f"#{rank} · distance {m['distance']:.4f} · {iso_ns(start_ns)[:22]} · "
                         f"catalog reference: {ref_text}", expanded=rank <= 2):
            a, b = st.columns(2)
            qn = (query_samples - query_samples.mean()) / (query_samples.std() or 1)
            mn = (samples - samples.mean()) / (samples.std() or 1)
            seconds = np.arange(len(qn)) / segment["rate"]
            frame = pd.concat([pd.DataFrame({"seconds from window start": seconds, "z-scored counts": qn, "series": "query"}),
                               pd.DataFrame({"seconds from window start": seconds, "z-scored counts": mn, "series": "match"})])
            a.altair_chart(line(frame, "seconds from window start", "z-scored counts", "Waveforms (per-window z-score)",
                                color_domain=("query", "match"), color_range=(QUERY, MATCH)), use_container_width=True)
            spectra = pd.concat([spectrum_frame(query_samples, segment["rate"], "query"),
                                 spectrum_frame(samples, mseg["rate"], "match")])
            spectra["amplitude (counts)"] = spectra["amplitude (counts)"].clip(lower=1e-6)
            b.altair_chart(line(spectra, "frequency (Hz)", "amplitude (counts)", "Amplitude spectra (raw counts)",
                                color_domain=("query", "match"), color_range=(QUERY, MATCH)).encode(
                y=alt.Y("amplitude (counts)", scale=alt.Scale(type="log"))), use_container_width=True)
            st.json({"recording_id": m["recording_id"], "interval_samples": [m["start"], m["end"]],
                     "utc": [iso_ns(start_ns), iso_ns(end_ns)], "split": mseg["split"],
                     "extractor_version": m.get("extractor_version", rep["extractor"]),
                     "source_sha256": m.get("source_sha256"), "acquisition": mseg["metadata"]},
                    expanded=False)


def about_tab():
    st.subheader("What this is and is not")
    st.markdown(
        "- A **research and investigation** tool. Waveform similarity is a lead for review, **not** an "
        "earthquake classifier, explosion or nuclear-test detector, or public warning service.\n"
        "- Triggers propose intervals (ObsPy recursive STA/LTA). Retrieval finds similar intervals. Catalog "
        "associations are a separate reference overlay with arrival-time uncertainty.\n"
        "- Matches are restricted to the same station/channel and compatible representation. "
        + " ".join(retrieval.UNAVAILABLE.values()) + "\n"
        "- Distances are representation distances, not probabilities.")
    st.subheader("Representation and preprocessing")
    st.json({"representation": rep, "preprocessing": investigate.PREPROCESSING, "detector": det,
             "association": definition["association"]})
    st.subheader("Data sources")
    st.markdown(f"- Waveforms and station metadata: {definition['provider']['name']} FDSN web services.\n"
                f"- Network: {definition['network_citation']['citation']}\n"
                f"- Catalog: {definition['catalog']['provider']} ({definition['catalog']['event_service']}).\n"
                f"- Manifest created {manifest['created_utc']}; definition sha256 "
                f"`{manifest['definition_sha256'][:16]}…`.")


tab_overview, tab_investigate, tab_about = st.tabs(["Stream overview", "Investigate an interval", "Limits & sources"])
with tab_overview:
    overview_tab()
with tab_investigate:
    investigate_tab()
with tab_about:
    about_tab()
