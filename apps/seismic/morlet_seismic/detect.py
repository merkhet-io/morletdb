"""Baseline candidate detector: ObsPy recursive STA/LTA on committed samples.

A trigger proposes an interval for review; it is not an event classification.
Catalog times are never inputs. The detector reads raw samples back from
MorletDB, filters a copy, and processes fixed blocks on each recording's sample
grid so results do not depend on chunk sizes, replay speed or restarts.

Block k covers onsets in [k*B, (k+1)*B). It is analyzed over
[k*B - warmup, (k+1)*B + post) once that much data is committed, or up to the
committed end once the segment is closed. Onsets within `warmup` of a segment
start are discarded: the long-term average is not established there, so that
time is reported as unmonitored rather than silently searched.
"""
import numpy as np


class Detector:
    def __init__(self, conn, client, config, dataset):
        self.conn, self.client, self.cfg, self.dataset = conn, client, config, dataset
        self.version = config["version"]
        self.stats = {"blocks": 0, "triggers": 0}

    def detect(self, samples, rate):
        """Return (on, off, peak_ratio, off_truncated) in local sample indices."""
        from obspy import Trace
        from obspy.signal.trigger import recursive_sta_lta, trigger_onset
        cfg = self.cfg
        trace = Trace(np.asarray(samples, dtype=float))
        trace.stats.sampling_rate = rate
        trace.detrend("demean")
        f = cfg["filter"]
        trace.filter(f["type"], freqmin=f["freqmin"], freqmax=f["freqmax"],
                     corners=f["corners"], zerophase=f["zerophase"])
        cft = recursive_sta_lta(trace.data, int(cfg["sta_s"] * rate), int(cfg["lta_s"] * rate))
        found = []
        for on, off in trigger_onset(cft, cfg["on"], cfg["off"]):
            on, off = int(on), int(off)
            if (off - on) / rate < cfg["min_duration_s"]:
                continue
            found.append((on, off, float(cft[on:off + 1].max()), off >= len(cft) - 1))
        return found

    def process(self, recording_id, closed=False):
        row = self.conn.execute(
            "SELECT stream,start_ns,sample_rate_hz FROM seismic.segments WHERE recording_id=%s",
            (recording_id,)).fetchone()
        stream, start_ns, rate = row
        end = self.client.stream_status(recording_id)["received_samples"]
        state = self.conn.execute(
            "SELECT next_block,closed FROM seismic.detector_progress WHERE recording_id=%s AND detector_version=%s",
            (recording_id, self.version)).fetchone()
        block = 0 if state is None else state[0]
        if state is not None and state[1]:
            return
        size = int(self.cfg["block_s"] * rate)
        warmup = int(self.cfg["warmup_s"] * rate)
        post = int(self.cfg["post_s"] * rate)
        finished = False
        while block * size < end:
            lo, hi = block * size, (block + 1) * size
            if hi + post <= end:
                b = hi + post
            elif closed:
                hi, b = min(hi, end), end
            else:
                break
            a = max(0, lo - warmup)
            samples = []
            for x in range(a, b, 32768):
                samples.extend(self.client.read_interval(recording_id, x, min(b, x + 32768))["samples"])
            rows = []
            for on, off, peak, truncated in self.detect(samples, rate):
                on, off = on + a, off + a
                if lo <= on < hi and on >= warmup:
                    rows.append((recording_id, on, off, self.dataset, stream,
                                 start_ns + round(on * 1e9 / rate), start_ns + round(off * 1e9 / rate),
                                 peak, truncated, self.version))
            with self.conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO seismic.triggers(recording_id,on_sample,off_sample,dataset,stream,on_ns,off_ns,"
                    "peak_ratio,off_truncated,detector_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                    " ON CONFLICT DO NOTHING", rows)
            block += 1
            self.stats["blocks"] += 1
            self.stats["triggers"] += len(rows)
            finished = closed and block * size >= end
            self._save(recording_id, block, finished)
            self.conn.commit()
        if closed and not finished:
            self._save(recording_id, block, True)
            self.conn.commit()

    def _save(self, recording_id, block, closed):
        self.conn.execute(
            "INSERT INTO seismic.detector_progress(recording_id,detector_version,next_block,closed)"
            " VALUES (%s,%s,%s,%s) ON CONFLICT (recording_id,detector_version)"
            " DO UPDATE SET next_block=EXCLUDED.next_block, closed=EXCLUDED.closed",
            (recording_id, self.version, block, closed))

    def on_commit(self, recording_ids):
        for recording_id in sorted(recording_ids):
            self.process(recording_id)

    def on_open(self, stream, recording_id):
        """A new segment ends earlier ones: analyze their trailing blocks now."""
        rows = self.conn.execute(
            "SELECT s.recording_id FROM seismic.segments s WHERE s.dataset=%s AND s.stream=%s"
            " AND s.recording_id<>%s AND NOT EXISTS (SELECT 1 FROM seismic.detector_progress d"
            " WHERE d.recording_id=s.recording_id AND d.detector_version=%s AND d.closed)",
            (self.dataset, stream, recording_id, self.version)).fetchall()
        for (other,) in rows:
            self.process(other, closed=True)

    def close_all(self, streams=None):
        """End of input: analyze the trailing partial block of every segment.

        Live mode never calls this for the current segment, which stays open.
        """
        rows = self.conn.execute(
            "SELECT recording_id FROM seismic.segments WHERE dataset=%s"
            + (" AND stream = ANY(%s)" if streams else "") + " ORDER BY start_ns",
            (self.dataset, list(streams)) if streams else (self.dataset,)).fetchall()
        for (recording_id,) in rows:
            self.process(recording_id, closed=True)
