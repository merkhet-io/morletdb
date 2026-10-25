"""Stream sources. Historical replay and live SeedLink emit the same Chunk type
into the same ingestion path; neither fills gaps nor modifies sample values.

Time is integer UTC nanoseconds of a chunk's first sample, taken from the
miniSEED header. Sample i of a chunk is at start_ns + i/rate. Ingestion maps
that to recording coordinates; adapters never relabel timestamps.
"""
from dataclasses import dataclass, field
import heapq
import queue
import threading
import time

import numpy as np

from .dataset import parse_time, stream_id


@dataclass(frozen=True)
class Chunk:
    stream: str            # NET.STA.LOC.CHA
    start_ns: int          # UTC of the first sample, integer nanoseconds
    sample_rate: float     # nominal header rate; never altered
    samples: np.ndarray    # raw values (digitizer counts), not filtered
    quality: str | None    # miniSEED data quality code (D, R, Q, M) when known
    origin: str            # file path or server that produced the chunk
    sequence: int          # adapter emission order
    emitted_wall: float = field(default_factory=time.time)  # local processing clock

    @property
    def end_ns(self):
        return self.start_ns + round(len(self.samples) * 1e9 / self.sample_rate)


class ReplayControl:
    """Thread-safe pacing for replay. speed=0 replays as fast as ingestion allows."""

    def __init__(self, speed=0.0):
        self._running = threading.Event()
        self._running.set()
        self._stop = threading.Event()
        self.speed = float(speed)

    def pause(self):
        self._running.clear()

    def resume(self):
        self._running.set()

    def stop(self):
        self._stop.set()
        self._running.set()

    @property
    def paused(self):
        return not self._running.is_set()

    @property
    def stopped(self):
        return self._stop.is_set()

    def wait(self):
        self._running.wait()
        return not self._stop.is_set()


class ReplaySource:
    """Replays cached miniSEED of a manifest in sample-time order across streams.

    Each trace is cut to its dataset period by sample index (no resampling), then
    split into chunks that never cross a trace boundary. Overlapping day files
    are replayed as-is: duplicates are for ingestion to verify, not to hide.
    """

    mode = "replay"

    def __init__(self, manifest, base, *, streams=None, periods=None, chunk_samples=3000,
                 control=None):
        if not 1 <= chunk_samples <= 32768:
            raise ValueError("chunk_samples must be 1..32768")
        self.manifest, self.base = manifest, base
        self.streams = streams
        self.periods = {p["name"]: p for p in manifest["definition"]["periods"]
                        if periods is None or p["name"] in periods}
        self.chunk_samples = chunk_samples
        self.control = control or ReplayControl()
        self.replay_time_ns = None   # sample time of the latest emitted chunk
        self.emitted = 0
        self.exhausted = False       # True only when every selected sample was emitted

    def files(self, stream):
        return sorted((w for w in self.manifest["waveforms"]
                       if w["stream"] == stream and w["period"] in self.periods),
                      key=lambda w: w["request_start"])

    def _stream_chunks(self, stream):
        from obspy import read
        for item in self.files(stream):
            period = self.periods[item["period"]]
            lo = int(parse_time(period["start"]).timestamp()) * 10**9
            hi = int(parse_time(period["end"]).timestamp()) * 10**9
            for trace in read(str(self.base / item["path"])):
                if trace.id != stream:
                    continue
                rate = float(trace.stats.sampling_rate)
                start_ns = trace.stats.starttime.ns
                step = 1e9 / rate
                # Keep samples whose own timestamps fall inside [lo, hi).
                first = max(0, int(np.ceil((lo - start_ns) / step)))
                last = min(len(trace.data), int(np.ceil((hi - start_ns) / step)))
                quality = trace.stats.mseed.get("dataquality") if "mseed" in trace.stats else None
                for offset in range(first, last, self.chunk_samples):
                    end = min(last, offset + self.chunk_samples)
                    yield (start_ns + round(offset * step), rate,
                           trace.data[offset:end], quality, item["path"])

    def chunks(self):
        selected = sorted({w["stream"] for w in self.manifest["waveforms"]}
                          if self.streams is None else self.streams)
        heads = []
        iterators = {s: self._stream_chunks(s) for s in selected}
        for name, iterator in iterators.items():
            item = next(iterator, None)
            if item is not None:
                heads.append((item[0], name, item))
        heapq.heapify(heads)
        wall_start, sample_start = None, None
        while heads and self.control.wait():
            start_ns, name, (_, rate, samples, quality, origin) = heapq.heappop(heads)
            following = next(iterators[name], None)
            if following is not None:
                heapq.heappush(heads, (following[0], name, following))
            if self.control.speed > 0:
                if wall_start is None:
                    wall_start, sample_start = time.monotonic(), start_ns
                target = wall_start + (start_ns - sample_start) / 1e9 / self.control.speed
                while not self.control.stopped and time.monotonic() < target:
                    if self.control.paused:
                        paused_at = time.monotonic()
                        self.control.wait()
                        wall_start += time.monotonic() - paused_at
                    time.sleep(min(0.2, max(0.0, target - time.monotonic())))
                    target = wall_start + (start_ns - sample_start) / 1e9 / self.control.speed
            self.replay_time_ns = start_ns
            self.emitted += 1
            yield Chunk(name, start_ns, rate, np.asarray(samples), quality, origin, self.emitted)
        self.exhausted = not heads and not self.control.stopped

    def status(self):
        return {"mode": self.mode, "paused": self.control.paused, "speed": self.control.speed,
                "stopped": self.control.stopped, "exhausted": self.exhausted,
                "replay_time_ns": self.replay_time_ns, "emitted_chunks": self.emitted}


class SeedLinkSource:
    """Opt-in live input from one SeedLink server connection.

    One connection carries all selected streams. On disconnect it reconnects
    with exponential backoff (10 s to 5 min) instead of hammering the server;
    it never polls an archive service. Never constructed by automated tests.
    """

    mode = "live"
    exhausted = False  # A live stream never ends naturally; recordings stay open.

    def __init__(self, server, streams, *, enabled=False, chunk_queue=1000):
        if not enabled:
            raise PermissionError("live SeedLink input must be explicitly enabled")
        self.server = server
        self.streams = list(streams)
        self.queue = queue.Queue(maxsize=chunk_queue)
        self.control = ReplayControl()
        self.reconnects = 0
        self.dropped = 0
        self.last_packet_wall = None
        self.last_sample_end_ns = {}
        self.last_error = None
        self.connected = False
        self._sequence = 0
        self._thread = None
        self._client = None

    def stop(self):
        """Stop reconnecting and close the open connection (unblocks run())."""
        self.control.stop()
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001 - already closed
                pass

    def _run(self):
        from obspy.clients.seedlink.easyseedlink import create_client
        delay = 10
        while not self.control.stopped:
            try:
                client = self._client = create_client(self.server, on_data=self._on_trace)
                for value in self.streams:
                    network, station, location, channel = value.split(".")
                    client.select_stream(network, station, f"{location}{channel}" if location else channel)
                self.connected = True
                delay = 10
                client.run()  # Blocks until the connection ends.
            except Exception as exc:  # noqa: BLE001 - reported to the operator
                self.last_error = f"{type(exc).__name__}: {exc}"
            self.connected = False
            if self.control.stopped:
                break
            self.reconnects += 1
            time.sleep(delay)
            delay = min(300, delay * 2)

    def _on_trace(self, trace):
        self.last_packet_wall = time.time()
        self._sequence += 1
        chunk = Chunk(trace.id, trace.stats.starttime.ns, float(trace.stats.sampling_rate),
                      np.asarray(trace.data), None, f"seedlink://{self.server}", self._sequence)
        self.last_sample_end_ns[trace.id] = chunk.end_ns
        try:
            self.queue.put_nowait(chunk)
        except queue.Full:
            self.dropped += 1  # Visible backlog failure; the gap remains a gap.

    def chunks(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="seedlink")
        self._thread.start()
        while not self.control.stopped:
            try:
                yield self.queue.get(timeout=1)
            except queue.Empty:
                continue

    def status(self):
        now = time.time()
        return {"mode": self.mode, "server": self.server, "connected": self.connected,
                "reconnects": self.reconnects, "dropped_chunks": self.dropped,
                "queue_backlog": self.queue.qsize(), "last_error": self.last_error,
                "packet_age_s": None if self.last_packet_wall is None else now - self.last_packet_wall,
                "sample_freshness_s": {s: now - end / 1e9 for s, end in self.last_sample_end_ns.items()}}


def manifest_streams(manifest):
    return sorted(stream_id(s) for s in manifest["definition"]["streams"])
