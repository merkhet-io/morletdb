# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""End to end: the real adapter code (Node.js, on the fake WinCC OA API of test/fake-winccoa.js) against the REAL
Python companion and a disposable MorletDB (the pinned release image in Docker, started by the companion's
tests/support.py and removed when the test process exits).

It checks that
- the namespace bootstrap creates the types and datapoints once, and a second run changes nothing;
- the initial values reach the companion as snapshots and changes keep their nanosecond source times exactly;
- the derived context lands on the samples computed independently here (ceil rule, integer arithmetic) on both
  channels, and the clean shutdown closes the open states at the last heartbeat;
- an investigation started from the slot's datapoints is answered and written back with its request_id;
- the adapter writes only to its namespace, and the token never appears in its log;
- a heartbeat that does not fit beside a pending gap goes after it, so derivation extends no state across the
  outage (#496; HeartbeatBeforeGap needs Node.js but no database).

    cd integrations/winccoa/adapter/test/e2e && python -m unittest -v test_e2e

Needs Docker and Node.js 22 or later; without them the test fails (it is never skipped).
"""
import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "companion" / "tests"))

import support  # noqa: E402  (puts the companion's src and the replay on the path)
from machine import CHANNELS, current  # noqa: E402
from morlet_winccoa import config as cfgmod  # noqa: E402
from morlet_winccoa import derive  # noqa: E402
from morlet_winccoa.observations import mark_finished, parse_message  # noqa: E402
from replay import write_history  # noqa: E402
from support import LOAD, SPEED, expected_samples  # noqa: E402
from writer import recording_id, write_acquisition  # noqa: E402

NODE = os.environ.get("NODE", "node")


class EndToEnd(unittest.TestCase):
    def test_adapter_against_the_real_companion(self):
        env = support.Env(support.database())
        self.addCleanup(env.drop)
        plan = dataclasses.replace(current(), acquisition_id=f"acq-{uuid.uuid4().hex[:8]}")
        origin = (time.time_ns() // 1000) * 1000 - 500_000_000          # the acquisition began 0.5 s ago
        with env.writer() as w:
            write_history(w, env.collection)
            write_acquisition(w, env.collection, plan, origin)
        env.start()
        impact = next(e for e in plan.events if e.label == "impact")
        k = impact.vib_start - impact.vib_start % 2048
        scenario = {
            "companion_url": f"http://127.0.0.1:{env.port}", "token_file": str(env.token_file), "asset": "rig-1",
            "acquisition": plan.acquisition_id,
            "datapoints": [
                {"dpe": SPEED, "element_type": "Int", "initial": 1800, "initial_stime_ns": str(origin)},
                {"dpe": LOAD, "element_type": "String", "initial": "high", "initial_stime_ns": str(origin)},
            ],
            "changes": [{"dpe": SPEED, "value": 1200, "hold_ms": 300}, {"dpe": LOAD, "value": "low", "hold_ms": 300}],
            "investigation": {"collection": env.collection, "recording_id": recording_id(plan, "vib"),
                              "start_sample": k, "end_sample": k + 4096},
        }
        tmp = Path(tempfile.mkdtemp(dir=env.tmp))
        (tmp / "scenario.json").write_text(json.dumps(scenario))
        proc = subprocess.run([NODE, str(HERE / "run.js"), str(tmp / "scenario.json"), str(tmp / "result.json")],
                              capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads((tmp / "result.json").read_text())

        # Bootstrap: created once, then nothing.
        self.assertEqual(out["bootstrap"]["first"]["code"], 0)
        self.assertEqual(sorted(out["bootstrap"]["created"]), sorted(
            ["type MorletAdapter", "type MorletInvestigation", "datapoint Morlet_Adapter", "datapoint Morlet_Inv_panel1"]))
        self.assertEqual(out["bootstrap"]["second"]["report"]["created"], [])
        self.assertEqual(out["bootstrap"]["second"]["code"], 0)

        with env.writer() as w:
            rows = w.execute(
                f"SELECT kind, dpe, value_text, source_ns, observed_ns, seq FROM {env.cfg.schema}.observation "
                "WHERE adapter_session = %s ORDER BY obs_id", (out["session"],)).fetchall()
        kinds = [r[0] for r in rows]
        # Start: a gap (the adapter was not watching), a heartbeat, then the initial values as snapshots.
        self.assertEqual(kinds[:4], ["gap", "heartbeat", "snapshot", "snapshot"])
        self.assertEqual(kinds[-1], "gap")                      # the clean shutdown
        self.assertEqual(kinds[-2], "heartbeat")                # ... right after a last heartbeat
        snaps = {r[1]: r for r in rows if r[0] == "snapshot"}
        self.assertEqual({d: (snaps[d][2], snaps[d][3]) for d in snaps},
                         {SPEED: ("1800", origin), LOAD: ('"high"', origin)})
        changes = [r for r in rows if r[0] == "change"]
        self.assertEqual([(r[1], r[2], str(r[3])) for r in changes],
                         [(SPEED, "1200", out["changes"][0]["stime_ns"]), (LOAD, '"low"', out["changes"][1]["stime_ns"])])
        self.assertTrue(all(int(c["stime_ns"]) % 1_000_000 for c in out["changes"]))   # nanoseconds kept
        last_hb = rows[-2][4]

        with env.writer() as w:
            mark_finished(w, env.cfg, env.collection, plan.acquisition_id, plan.end_ns)
            w.commit()
        env.derive(plan.acquisition_id)

        def t(ns):
            return int(ns) - origin
        c_speed, c_load = (t(c["stime_ns"]) for c in out["changes"])
        intervals = {
            "speed": [(max(t(snaps[SPEED][3]), t(snaps[SPEED][4])), c_speed, 1800), (c_speed, t(last_hb), 1200)],
            "load": [(max(t(snaps[LOAD][3]), t(snaps[LOAD][4])), c_load, "high"), (c_load, t(last_hb), "low")],
        }
        expected = []
        for role, (rate, _channel, _unit, offset, *_rest) in CHANNELS.items():
            for key, ivs in intervals.items():
                for t0, t1, value in ivs:
                    a, b = expected_samples(rate, offset, t0, t1)
                    if b > a:
                        expected.append((recording_id(plan, role), key, value, a, b))
        with env.writer() as w:
            got = w.execute(f"SELECT recording_id, key, value, start_sample, end_sample FROM {env.collection}_ctx "
                            "WHERE recording_id LIKE %s AND NOT retracted ORDER BY 1, 2, 4",
                            (plan.acquisition_id + "-%",)).fetchall()
        self.assertEqual(sorted(got), sorted(expected))
        self.assertEqual(len(got), 8)                           # 2 keys x 2 states x 2 channels

        inv = out["investigation"]
        self.assertEqual(inv["state"], "done", inv)
        self.assertEqual(inv["status"], "ok", inv)
        result = json.loads(inv["result"])
        self.assertEqual(result["request_id"], inv["requestId"])
        self.assertEqual(result["slot"], "panel1")
        self.assertGreater(inv["matchCount"], 0)
        self.assertEqual(inv["matchCount"], len(result["matches"]))
        self.assertEqual(result["source"]["start_sample"], k)

        self.assertTrue(all(n.startswith(("Morlet_Adapter.", "Morlet_Inv_panel1.")) for n in out["namespaceWrites"]),
                        out["namespaceWrites"])
        self.assertEqual(out["status"]["state"], "stopped")
        self.assertEqual(out["status"]["companion"], "ok")
        log = "\n".join(out["logs"])
        self.assertNotIn(env.token, log)
        self.assertIn(out["status"]["tokenFingerprint"], log)


class HeartbeatBeforeGap(unittest.TestCase):
    """#496: the observations the real adapter sends when a heartbeat does not fit beside a pending gap, parsed and
    derived by the real companion code (parse_message, derive.compute; no database needed). A heartbeat sent before
    the gap counted as contact during the outage and extended the open state across it."""

    def derived(self, bound: str):
        proc = subprocess.run([NODE, str(HERE.parent / "heartbeat-gap.js"), bound], capture_output=True, text=True,
                              timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        cfg = cfgmod.validate(cfgmod.Config(
            dsn="host=127.0.0.1 dbname=morlet user=wc", token_file="/nonexistent",
            assets=(cfgmod.Asset("rig-1", "plant.rig", "rig-1"),),
            datapoints=(cfgmod.Datapoint(SPEED, "rig-1", "speed"),)))
        rows = []
        for observations in out["posts"]:                # committed in sending order: obs_id follows it
            _, parsed = parse_message(cfg, {"schema": "morlet-winccoa/1", "session": "winccoa-496",
                                            "observations": observations})
            for o in parsed:
                rows.append((len(rows) + 1, o.kind, o.asset, o.key, o.dpe, o.value_json is None, o.value_type,
                             o.value_text, o.source_ns, o.quality_status, o.observed_ns, o.observed_ns, o.rtt_ns))
        return rows, derive.compute(cfg, rows, 0, None)

    def test_no_context_across_the_disconnect(self):
        for bound in ("count", "bytes"):
            with self.subTest(bound=bound):
                rows, outcomes = self.derived(bound)
                kinds = [r[1] for r in rows]
                (speed,) = outcomes
                # The last contact before the gap is the change itself (observed at 1 s): nothing is known after
                # it, so the state yields no context (before the fix: a batch [1 s, 10 s) across the outage).
                self.assertNotEqual(speed.batch_id, "pending", speed)
                self.assertEqual((speed.t_start_ns, speed.t_end_ns, speed.reason),
                                 (1_000_000_000, 1_000_000_000, "outside_recording"))
                self.assertEqual(speed.closed_by, kinds.index("gap") + 1)
                self.assertEqual(kinds, ["heartbeat", "change", "gap", "heartbeat"])


if __name__ == "__main__":
    unittest.main()
