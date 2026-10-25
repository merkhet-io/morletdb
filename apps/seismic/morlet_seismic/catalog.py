"""Catalog reference overlay: evaluation and review evidence, never detector input.

Catalog origin time is not station arrival time. Each event is mapped to each
station with IASP91 travel times (ObsPy TauP) for the first P and S arrivals.
A trigger is associated when its onset lies in [P - pre_p, S + post_s]
(regional, < 10 degrees) or [P - pre_p, P + teleseismic_post_p] (teleseismic).
Location, depth and model errors make this window approximate; associations
are reported with their residuals and are not ground-truth event labels.
"""
from functools import lru_cache

from obspy import UTCDateTime
from obspy.geodetics import locations2degrees


# Direct first-arrival phase names only; depth phases (pP, sP) are excluded.
P_PHASES = {"p", "P", "Pn", "Pg", "Pb", "Pdiff", "PKP", "PKIKP", "PKiKP"}
S_PHASES = {"s", "S", "Sn", "Sg", "Sb", "Sdiff"}


@lru_cache(maxsize=1)
def _model(name):
    from obspy.taup import TauPyModel
    return TauPyModel(model=name)


def catalog_events(manifest):
    events = {}
    for item in manifest["catalogs"]:
        for event in item["events"]:
            events.setdefault(event["id"], {**event, "source": item["path"],
                                            "query": item["name"]})
    return sorted(events.values(), key=lambda e: e["origin_time"])


def station_coordinates(manifest):
    coords = {}
    for item in manifest["stations"]:
        for channel in item["channels"]:
            coords[channel["id"]] = (channel["latitude"], channel["longitude"], channel["elevation_m"])
    return coords


def predicted_arrivals(manifest, config):
    """Per (event, stream): origin, distance and predicted first P/S times (ns)."""
    model = _model(config["model"])
    coords = station_coordinates(manifest)
    rows = []
    for event in catalog_events(manifest):
        origin = UTCDateTime(event["origin_time"])
        depth = max(0.0, event["depth_km"] or 0.0)
        for stream, (lat, lon, _) in sorted(coords.items()):
            distance = locations2degrees(event["latitude"], event["longitude"], lat, lon)
            arrivals = model.get_travel_times(depth, distance, phase_list=["ttp", "tts"])
            p = min((a.time for a in arrivals if a.name in P_PHASES), default=None)
            s = min((a.time for a in arrivals if a.name in S_PHASES), default=None)
            if p is None:
                continue
            regional = bool(distance < 10)
            lo = p - config["pre_p_s"]
            hi = (s + config["post_s_s"]) if (regional and s is not None) else p + config["teleseismic_post_p_s"]
            rows.append({"event_id": event["id"], "stream": stream, "magnitude": event["magnitude"],
                         "magnitude_type": event["magnitude_type"], "depth_km": event["depth_km"],
                         "latitude": event["latitude"], "longitude": event["longitude"],
                         "origin_ns": origin.ns, "distance_deg": float(distance), "regional": regional,
                         "p_ns": origin.ns + round(p * 1e9),
                         "s_ns": None if s is None else origin.ns + round(s * 1e9),
                         "window_ns": (origin.ns + round(lo * 1e9), origin.ns + round(hi * 1e9)),
                         "description": event.get("description"), "catalog_query": event["query"]})
    return rows


def associate(triggers, arrivals):
    """Return {trigger key: [association...]} sorted by |onset - predicted P|."""
    by_stream = {}
    for row in arrivals:
        by_stream.setdefault(row["stream"], []).append(row)
    result = {}
    for trigger in triggers:
        matches = []
        for row in by_stream.get(trigger["stream"], []):
            lo, hi = row["window_ns"]
            if lo <= trigger["on_ns"] <= hi:
                matches.append({**row, "p_residual_s": (trigger["on_ns"] - row["p_ns"]) / 1e9})
        matches.sort(key=lambda m: abs(m["p_residual_s"]))
        result[(trigger["recording_id"], trigger["on_sample"])] = matches
    return result


def monitored(segments, stream, moment_ns, warmup_s):
    """Was the detector monitoring this stream at moment_ns (committed, past warm-up)?"""
    for segment in segments:
        if segment["stream"] != stream:
            continue
        start = segment["start_ns"] + round(warmup_s * 1e9)
        if start <= moment_ns < segment["end_ns"]:
            return True
    return False
