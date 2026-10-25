"""Reproducible public waveform dataset: download once, then verify and replay offline.

Raw miniSEED, StationXML and QuakeML bytes are cached unchanged. The manifest
records exact request URLs, checksums, trace summaries and retrieval times.
A failed request is recorded and fails the download; nothing is substituted.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

MANIFEST_VERSION = "seismic-dataset-manifest-v1"
USER_AGENT = "morletdb-seismic-investigation/0.1 (local research replay)"
REPO = Path(__file__).resolve().parents[3]
DEFAULT_DATA = REPO / "data" / "seismic"


def load_definition(path):
    definition = json.loads(Path(path).read_text())
    for period in definition["periods"]:
        if parse_time(period["end"]) <= parse_time(period["start"]):
            raise ValueError(f"empty period {period['name']}")
    starts = sorted((parse_time(p["start"]), parse_time(p["end"])) for p in definition["periods"])
    if any(a[1] > b[0] for a, b in zip(starts, starts[1:])):
        raise ValueError("dataset periods must not overlap")
    return definition


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def stream_id(stream):
    return f"{stream['network']}.{stream['station']}.{stream['location']}.{stream['channel']}"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def dataset_dir(definition, root=DEFAULT_DATA):
    return Path(root) / definition["id"]


def _fetch(url, target, attempts=3):
    """Download to target atomically. 204 (no data) is an explicit failure."""
    target.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=300) as response:
                if response.status == 204:
                    raise ValueError("provider returned 204: no data for request")
                body = response.read()
            if not body:
                raise ValueError("provider returned an empty body")
            partial = target.with_suffix(target.suffix + ".partial")
            partial.write_bytes(body)
            partial.replace(target)
            return
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last = exc
            if isinstance(exc, ValueError):
                break
            time.sleep(5 * (attempt + 1))  # Modest backoff; requests are sequential.
    raise RuntimeError(f"download failed for {url}: {last}")


def _days(period):
    start, end = parse_time(period["start"]), parse_time(period["end"])
    cursor = start
    while cursor < end:
        following = min(end, (cursor + timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                                                  microsecond=0))
        yield cursor, following
        cursor = following


def waveform_requests(definition):
    """Deterministic one-day requests; files may contain records beyond the bounds."""
    provider = definition["provider"]["dataselect"]
    for stream in definition["streams"]:
        for period in definition["periods"]:
            for start, end in _days(period):
                params = {"network": stream["network"], "station": stream["station"],
                          "location": stream["location"] or "--", "channel": stream["channel"],
                          "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
                          "endtime": end.strftime("%Y-%m-%dT%H:%M:%S")}
                name = f"{stream_id(stream)}/{start:%Y-%m-%dT%H%M%S}.mseed"
                yield {"stream": stream_id(stream), "period": period["name"],
                       "split": period["split"], "request_start": iso(start),
                       "request_end": iso(end), "params": params,
                       "url": provider + "?" + urllib.parse.urlencode(params),
                       "path": f"raw/{name}"}


def station_requests(definition):
    provider = definition["provider"]["station"]
    first = min(parse_time(p["start"]) for p in definition["periods"])
    last = max(parse_time(p["end"]) for p in definition["periods"])
    for stream in definition["streams"]:
        params = {"network": stream["network"], "station": stream["station"],
                  "location": stream["location"] or "--", "channel": stream["channel"],
                  "starttime": first.strftime("%Y-%m-%dT%H:%M:%S"),
                  "endtime": last.strftime("%Y-%m-%dT%H:%M:%S"), "level": "response"}
        yield {"stream": stream_id(stream), "params": params,
               "url": provider + "?" + urllib.parse.urlencode(params),
               "path": f"stationxml/{stream_id(stream)}.xml"}


def catalog_requests(definition):
    catalog = definition["catalog"]
    for period in definition["periods"]:
        for query in catalog["queries"]:
            params = {key: value for key, value in query.items() if key != "name"}
            # Pad by an hour: an earlier origin can arrive inside the period.
            start = parse_time(period["start"]) - timedelta(hours=1)
            params.update({"starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
                           "endtime": parse_time(period["end"]).strftime("%Y-%m-%dT%H:%M:%S"),
                           "format": "xml", "orderby": "time-asc"})
            yield {"name": query["name"], "period": period["name"], "params": params,
                   "url": catalog["event_service"] + "?" + urllib.parse.urlencode(params),
                   "path": f"catalog/usgs-{query['name']}-{period['name']}.xml"}


def summarize_waveform(path):
    from obspy import read
    traces = []
    for trace in read(str(path)):
        stats = trace.stats
        traces.append({"id": trace.id, "start": str(stats.starttime), "end": str(stats.endtime),
                       "npts": int(stats.npts), "sampling_rate": float(stats.sampling_rate),
                       "dtype": str(trace.data.dtype),
                       "quality": stats.mseed.get("dataquality") if "mseed" in stats else None,
                       "encoding": stats.mseed.get("encoding") if "mseed" in stats else None})
    return traces


def summarize_station(path):
    from obspy import read_inventory
    channels = []
    for network in read_inventory(str(path)):
        for station in network:
            for channel in station:
                sensitivity = channel.response.instrument_sensitivity if channel.response else None
                channels.append({
                    "id": f"{network.code}.{station.code}.{channel.location_code}.{channel.code}",
                    "start": str(channel.start_date), "end": str(channel.end_date),
                    "latitude": channel.latitude, "longitude": channel.longitude,
                    "elevation_m": channel.elevation, "depth_m": channel.depth,
                    "azimuth": channel.azimuth, "dip": channel.dip,
                    "sample_rate": float(channel.sample_rate),
                    "sensor": channel.sensor.description if channel.sensor else None,
                    "sensitivity": None if sensitivity is None else sensitivity.value,
                    "sensitivity_frequency_hz": None if sensitivity is None else sensitivity.frequency,
                    "input_units": None if sensitivity is None else sensitivity.input_units,
                    "output_units": None if sensitivity is None else sensitivity.output_units})
    return channels


def summarize_catalog(path):
    from obspy import read_events
    events = []
    for event in read_events(str(path)):
        origin = event.preferred_origin() or event.origins[0]
        magnitude = event.preferred_magnitude() or (event.magnitudes[0] if event.magnitudes else None)
        resource = str(event.resource_id)
        events.append({"id": resource.split("eventid=")[-1].split("&")[0] if "eventid=" in resource else resource,
                       "resource_id": resource, "origin_time": str(origin.time),
                       "latitude": origin.latitude, "longitude": origin.longitude,
                       "depth_km": None if origin.depth is None else origin.depth / 1000,
                       "magnitude": None if magnitude is None else magnitude.mag,
                       "magnitude_type": None if magnitude is None else magnitude.magnitude_type,
                       "event_type": str(event.event_type) if event.event_type else None,
                       "description": event.event_descriptions[0].text if event.event_descriptions else None})
    return events


def download(definition_path, root=DEFAULT_DATA, *, refresh=False, log=print):
    """Fetch missing files (or all with refresh) and write a complete manifest."""
    definition = load_definition(definition_path)
    base = dataset_dir(definition, root)
    previous = {}
    manifest_path = base / "manifest.json"
    if manifest_path.exists():
        for section in ("waveforms", "stations", "catalogs"):
            for item in json.loads(manifest_path.read_text()).get(section, []):
                previous[item["path"]] = item
    entries = {"waveforms": list(waveform_requests(definition)),
               "stations": list(station_requests(definition)),
               "catalogs": list(catalog_requests(definition))}
    for section, items in entries.items():
        for item in items:
            target = base / item["path"]
            known = previous.get(item["path"])
            if refresh or not target.exists():
                log(f"GET {item['url']}")
                _fetch(item["url"], target)
                item["retrieved_utc"] = iso(datetime.now(timezone.utc))
            else:
                if known and known["sha256"] != sha256(target):
                    raise ValueError(f"cached file changed since manifest: {item['path']}")
                item["retrieved_utc"] = known["retrieved_utc"] if known else "unknown (pre-existing cache)"
            item["sha256"] = sha256(target)
            item["bytes"] = target.stat().st_size
            if section == "waveforms":
                item["traces"] = summarize_waveform(target)
                if not item["traces"]:
                    raise ValueError(f"no traces in {item['path']}")
            elif section == "stations":
                item["channels"] = summarize_station(target)
            else:
                item["events"] = summarize_catalog(target)
    manifest = {"manifest_version": MANIFEST_VERSION, "definition": definition,
                "definition_sha256": hashlib.sha256(Path(definition_path).read_bytes()).hexdigest(),
                "created_utc": iso(datetime.now(timezone.utc)),
                "software": software_versions(), **entries}
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest_path


def software_versions():
    import platform
    import numpy
    import obspy
    import scipy
    return {"python": platform.python_version(), "obspy": obspy.__version__,
            "numpy": numpy.__version__, "scipy": scipy.__version__,
            "platform": platform.platform()}


def load_manifest(definition, root=DEFAULT_DATA, *, verify=True):
    """Offline: read and optionally checksum-verify every cached file."""
    base = dataset_dir(definition, root)
    path = base / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"no manifest at {path}; run the download command first")
    manifest = json.loads(path.read_text())
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise ValueError("unsupported manifest version")
    if manifest["definition"]["id"] != definition["id"]:
        raise ValueError("manifest belongs to another dataset")
    if verify:
        for section in ("waveforms", "stations", "catalogs"):
            for item in manifest[section]:
                target = base / item["path"]
                if not target.exists() or sha256(target) != item["sha256"]:
                    raise ValueError(f"missing or modified cached file: {item['path']}")
    return manifest
