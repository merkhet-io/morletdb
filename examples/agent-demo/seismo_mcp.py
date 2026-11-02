#!/usr/bin/env python3
"""Seismology reference tools for the agent demo, as a second MCP server beside morletdb.

  python examples/agent-demo/seismo_mcp.py        # stdio

Offline and read-only: it answers from files in reference/ and computes travel times with ObsPy TauP, so the
agent can relate what it finds in the recordings to catalogued earthquakes and to when their waves should
arrive. It knows nothing about the recordings; morletdb does.

- reference/emsc_events.txt: the EMSC catalogue (seismicportal.eu FDSN event service, text format) for
  2025-03-11..14 within 3 degrees of (-21.2, -69.8), as downloaded for evaluation E5
  (evaluation/task_relevance/data). EMSC revises its catalogue; this is a snapshot.
- reference/stations.txt: coordinates of the four CX stations (GEOFON station service).
"""
import functools
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from obspy import UTCDateTime
from obspy.geodetics import gps2dist_azimuth, kilometers2degrees
from obspy.taup import TauPyModel

REF = Path(__file__).resolve().parent / "reference"
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

mcp = MCPServer("seismo", instructions="""Seismology reference data for the MorletDB agent demo: station
coordinates, a snapshot of the EMSC earthquake catalogue for 11-14 March 2025 in northern Chile, and predicted
P and S arrival times (iasp91 model). Use it with the morletdb server: predict when an event's waves reach a
station, then locate_time and inspect the recording there; or check whether a search hit lines up with a
catalogued event. Predictions are model times: real arrivals can differ by a few seconds, more for deep or
distant events.""")


def safe(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, KeyError, TypeError) as exc:
            raise ToolError(str(exc)) from exc
    return wrapper


def load_stations():
    out = {}
    for line in (REF / "stations.txt").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        f = line.split("|")
        out[f[1]] = {"network": f[0], "station": f[1], "latitude": float(f[2]), "longitude": float(f[3]),
                     "elevation_m": float(f[4]), "site": f[5]}
    return out


def load_events():
    out = []
    for line in (REF / "emsc_events.txt").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        f = line.split("|")
        out.append({"event_id": f[0], "origin_utc": str(UTCDateTime(f[1])), "latitude": float(f[2]),
                    "longitude": float(f[3]), "depth_km": float(f[4]), "magnitude": float(f[10]),
                    "magnitude_type": f[9], "region": f[12].title()})
    return out


STATIONS = load_stations()
EVENTS = load_events()
MODEL = None


def station(name):
    key = name.split(".")[-1].upper()
    if key not in STATIONS:
        raise ValueError(f"unknown station {name!r}; known: {', '.join(STATIONS)}")
    return STATIONS[key]


def distance_km(lat1, lon1, lat2, lon2):
    return gps2dist_azimuth(lat1, lon1, lat2, lon2)[0] / 1000.0


def arrivals(depth_km, dist_km):
    global MODEL
    MODEL = MODEL or TauPyModel("iasp91")
    times = MODEL.get_travel_times(max(0.0, depth_km), kilometers2degrees(dist_km), ["P", "p", "S", "s"])
    p = min((a.time for a in times if a.name in ("P", "p")), default=None)
    s = min((a.time for a in times if a.name in ("S", "s")), default=None)
    return (None if p is None else float(p)), (None if s is None else float(s))


@mcp.tool(annotations=READ_ONLY)
@safe
def list_stations() -> dict:
    """The demo's seismic stations with coordinates and site names."""
    return {"stations": list(STATIONS.values())}


@mcp.tool(annotations=READ_ONLY)
@safe
def catalog_events(start_utc: str = "2025-03-11T00:00:00Z", end_utc: str = "2025-03-15T00:00:00Z",
                   min_magnitude: float = 0.0, station: str = "", max_distance_km: float = 0.0) -> dict:
    """Catalogued earthquakes (EMSC snapshot, 11-14 March 2025, within about 330 km of the stations) with
    origin time, location, depth and magnitude. With station, each event also gets its distance to that
    station and predicted P and S arrival times there; max_distance_km then limits the distance."""
    t0, t1 = UTCDateTime(start_utc), UTCDateTime(end_utc)
    st = globals()["station"](station) if station else None
    out = []
    for e in EVENTS:
        if not (t0 <= UTCDateTime(e["origin_utc"]) < t1 and e["magnitude"] >= min_magnitude):
            continue
        e = dict(e)
        if st:
            d = distance_km(e["latitude"], e["longitude"], st["latitude"], st["longitude"])
            if max_distance_km and d > max_distance_km:
                continue
            p, s = arrivals(e["depth_km"], d)
            origin = UTCDateTime(e["origin_utc"])
            e.update({"station": st["station"], "distance_km": round(d, 1),
                      "p_arrival_utc": str(origin + p) if p is not None else None,
                      "s_arrival_utc": str(origin + s) if s is not None else None})
        out.append(e)
    return {"source": "EMSC (seismicportal.eu), snapshot", "count": len(out),
            "events": sorted(out, key=lambda e: e["origin_utc"])}


@mcp.tool(annotations=READ_ONLY)
@safe
def predict_arrivals(origin_utc: str, latitude: float, longitude: float, depth_km: float,
                     stations: str = "") -> dict:
    """Predicted P and S arrival times (iasp91) of an earthquake at the stations (comma-separated names;
    default all), with distance and S-P time. A larger S-P time means a more distant source: roughly 8 km
    per second of S-P for shallow local events."""
    origin = UTCDateTime(origin_utc)
    names = [n.strip() for n in stations.split(",") if n.strip()] or list(STATIONS)
    out = []
    for n in names:
        st = station(n)
        d = distance_km(latitude, longitude, st["latitude"], st["longitude"])
        p, s = arrivals(depth_km, d)
        out.append({"station": st["station"], "distance_km": round(d, 1),
                    "p_arrival_utc": str(origin + p) if p is not None else None,
                    "s_arrival_utc": str(origin + s) if s is not None else None,
                    "s_minus_p_s": round(float(s - p), 2) if p is not None and s is not None else None})
    return {"origin_utc": str(origin), "model": "iasp91", "arrivals": out}


if __name__ == "__main__":
    mcp.run("stdio")
