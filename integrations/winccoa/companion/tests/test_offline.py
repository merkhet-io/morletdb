# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""Configuration, token files, protocol validation and the response bound: the parts that need no database."""
import copy
import dataclasses
import json
import logging
import os
import secrets
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "replay"))

from morlet_winccoa import config as cfgmod  # noqa: E402
from morlet_winccoa import investigate as inv  # noqa: E402
from morlet_winccoa import observations as obs  # noqa: E402
from morlet_winccoa.auth import Authenticator, TokenFileError, fingerprint, read_tokens  # noqa: E402
from morlet_winccoa.investigate import parse_request  # noqa: E402

SPEED = "System1:Rig1.speed"


def config(**changes):
    base = dict(dsn="host=127.0.0.1 dbname=morlet user=wc", token_file="/nonexistent",
                assets=(cfgmod.Asset("rig-1", "plant.rig", "rig-1"),),
                datapoints=(cfgmod.Datapoint(SPEED, "rig-1", "speed"),))
    base.update(changes)
    return cfgmod.validate(cfgmod.Config(**base))


class ConfigTests(unittest.TestCase):
    def test_defaults_are_the_design_values(self):
        c = config()
        self.assertEqual((c.listen_host, c.listen_port, c.schema), ("127.0.0.1", 8770, "morlet_winccoa"))
        lim = c.limits
        self.assertEqual((lim.observations_per_message, lim.body_bytes, lim.datapoints), (1000, 1 << 20, 500))
        self.assertEqual((lim.top_k_default, lim.top_k_max, lim.investigations_total), (10, 50, 4))
        self.assertEqual((lim.response_bytes, lim.overview_buckets), (256 * 1024, 16))
        self.assertEqual((lim.statement_timeout_ms, lim.http_deadline_ms, lim.max_candidates), (10_000, 15_000, 1_000_000))
        self.assertEqual((lim.window_min, lim.window_default, lim.window_max, lim.writer_batch_samples),
                         (512, 4096, 32_768, 65_536))
        self.assertEqual((c.clock.bound_ms, c.clock.heartbeat_interval_s, c.clock.allow_observed_time_basis),
                         (50.0, 10.0, False))

    def test_refuses_a_password_in_the_dsn(self):
        for dsn in ("host=x password=secret", "postgresql://u:secret@h/db", "postgresql://u@h/db?password=s"):
            with self.assertRaises(cfgmod.ConfigError):
                config(dsn=dsn)

    def test_binds_loopback_only(self):
        with self.assertRaises(cfgmod.ConfigError):
            config(listen_host="0.0.0.0")
        config(listen_host="127.0.0.1")

    def test_viewer_links_only_on_loopback(self):
        config(viewer_base_url="http://127.0.0.1:8765")
        with self.assertRaises(cfgmod.ConfigError):
            config(viewer_base_url="http://viewer.example:8765")

    def test_observed_time_basis_is_opt_in(self):
        dp = (cfgmod.Datapoint(SPEED, "rig-1", "speed", time_basis="observed"),)
        with self.assertRaises(cfgmod.ConfigError):
            config(datapoints=dp)
        config(datapoints=dp, clock=cfgmod.Clock(allow_observed_time_basis=True))

    def test_datapoint_limit(self):
        dps = tuple(cfgmod.Datapoint(f"S:dp{i}", "rig-1", f"k{i}") for i in range(3))
        with self.assertRaises(cfgmod.ConfigError):
            config(datapoints=dps, limits=cfgmod.Limits(datapoints=2))

    def test_toml(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write('[service]\ndsn = "host=127.0.0.1 user=wc"\ntoken_file = "/t"\nlisten = "127.0.0.1:9000"\n'
                    '[limits]\ntop_k_max = 20\n[[assets]]\nname = "a"\ncollection = "p.r"\nmachine = "m"\n'
                    '[[datapoints]]\ndpe = "S:x"\nasset = "a"\nkey = "x"\n')
        try:
            c = cfgmod.load(f.name)
            self.assertEqual((c.listen_port, c.limits.top_k_max, c.datapoint("S:x").key), (9000, 20, "x"))
        finally:
            os.unlink(f.name)
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.from_dict({"service": {"dsn": "", "token_file": ""}, "limits": {"no_such": 1}})

    def test_example_config_loads(self):
        example = Path(__file__).resolve().parents[1] / "companion.example.toml"
        c = cfgmod.load(example)
        self.assertTrue(c.assets and c.datapoints)


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "token")

    def write(self, *tokens, mode=0o600):
        with open(self.path, "w") as f:
            f.write("\n".join(tokens) + "\n")
        os.chmod(self.path, mode)

    def test_mode_must_be_0600(self):
        self.write(secrets.token_hex(32), mode=0o640)
        with self.assertRaises(TokenFileError):
            read_tokens(self.path)
        os.chmod(self.path, 0o600)
        read_tokens(self.path)

    def test_short_tokens_refused(self):
        self.write("short")
        with self.assertRaises(TokenFileError):
            read_tokens(self.path)

    def test_two_tokens_accepted_and_reload_on_change(self):
        a, b, c = (secrets.token_hex(32) for _ in range(3))
        self.write(a, b)
        auth = Authenticator(self.path)
        self.assertEqual(auth.check(f"Bearer {a}", "127.0.0.1"), "ok")
        self.assertEqual(auth.check(f"Bearer {b}", "127.0.0.1"), "ok")
        time.sleep(0.01)
        self.write(c)
        os.utime(self.path, ns=(time.time_ns(), time.time_ns() + 1_000_000_000))
        self.assertEqual(auth.check(f"Bearer {c}", "127.0.0.1"), "ok")
        self.assertEqual(auth.check(f"Bearer {a}", "127.0.0.1"), "unauthorized")

    def test_widened_mode_on_reload_fails_closed(self):
        a = secrets.token_hex(32)
        self.write(a)
        auth = Authenticator(self.path)
        os.chmod(self.path, 0o644)
        os.utime(self.path, ns=(time.time_ns(), time.time_ns() + 1_000_000_000))
        self.assertEqual(auth.check(f"Bearer {a}", "127.0.0.1"), "unauthorized")

    def test_backoff_after_five_failures_and_logs_only_fingerprints(self):
        a = secrets.token_hex(32)
        wrong = secrets.token_hex(32)
        self.write(a)
        now = [1000.0]
        auth = Authenticator(self.path, failures_per_minute=5, backoff_s=60, clock=lambda: now[0])
        with self.assertLogs("morlet_winccoa.auth", level=logging.DEBUG) as logs:
            for _ in range(5):
                self.assertEqual(auth.check(f"Bearer {wrong}", "127.0.0.9"), "unauthorized")
            self.assertEqual(auth.check(f"Bearer {a}", "127.0.0.9"), "backoff")
            self.assertEqual(auth.check(f"Bearer {a}", "127.0.0.1"), "ok")    # other addresses are not blocked
            now[0] += 61
            self.assertEqual(auth.check(f"Bearer {a}", "127.0.0.9"), "ok")
        text = "\n".join(logs.output)
        self.assertNotIn(a, text)
        self.assertNotIn(wrong, text)
        self.assertIn(fingerprint(wrong), text)

    def test_failures_spread_over_more_than_a_minute_do_not_block(self):
        a = secrets.token_hex(32)
        self.write(a)
        now = [0.0]
        auth = Authenticator(self.path, clock=lambda: now[0])
        for _ in range(10):
            self.assertEqual(auth.check("Bearer nope", "127.0.0.9"), "unauthorized")
            now[0] += 20
        self.assertEqual(auth.check(f"Bearer {a}", "127.0.0.9"), "ok")


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config()

    def obs(self, **o):
        base = {"seq": 1, "kind": "change", "acquisition": "a1", "dpe": SPEED, "value_text": "1800",
                "value_type": "int", "source_ns": "1790812800000000000", "observed_ns": "1790812800001000000",
                "quality": {"good": True}}
        base.update(o)
        return {k: v for k, v in base.items() if v is not None}

    def test_valid_observation(self):
        o = obs.parse_observation(self.cfg, self.obs())
        self.assertEqual((o.key, o.value_json, o.source_ns, o.quality_status), ("speed", 1800, 1790812800000000000, "good"))

    def test_value_text_is_kept_exactly(self):
        for text, vtype, value in (("-0.0", "float", -0.0), ("NaN", "float", None), ("1e300", "float", 1e300),
                                   ("12345678901234567890", "int", 12345678901234567890), ('"a\\u00e9"', "string", "aé"),
                                   ("true", "bool", True), ('{"x":[1,2]}', "json", {"x": [1, 2]})):
            o = obs.parse_observation(self.cfg, self.obs(value_text=text, value_type=vtype))
            self.assertEqual(o.value_text, text)
            self.assertEqual(o.value_json, value)

    def test_malformed(self):
        bad = [self.obs(seq=-1), self.obs(seq=True), self.obs(kind="other"), self.obs(source_ns=1790812800000000000),
               self.obs(source_ns="9223372036854775808"), self.obs(observed_ns=None), self.obs(value_text="1.5"),
               self.obs(value_type="int", value_text="abc"), self.obs(value_type="bool", value_text="1"),
               self.obs(quality="good"), self.obs(quality={"good": "yes"}), self.obs(extra=1),
               self.obs(acquisition=""), self.obs(value_text="Infinity", value_type="json"),
               self.obs(kind="heartbeat"), self.obs(source_ns=None, source_status="ok")]
        for b in bad:
            with self.assertRaises(obs.ProtocolError, msg=str(b)):
                obs.parse_observation(self.cfg, b)

    def test_unconfigured_names_are_forbidden(self):
        with self.assertRaises(obs.ForbiddenName):
            obs.parse_observation(self.cfg, self.obs(dpe="System1:Other.x"))
        with self.assertRaises(obs.ForbiddenName):
            obs.parse_observation(self.cfg, {"seq": 1, "kind": "heartbeat", "acquisition": "a", "asset": "other",
                                             "observed_ns": "1"})

    def test_missing_source_time_and_quality(self):
        o = obs.parse_observation(self.cfg, self.obs(source_ns=None, quality=None))
        self.assertEqual((o.source_status, o.quality_status, o.quality), ("missing", "missing", None))
        o = obs.parse_observation(self.cfg, self.obs(source_ns=None, source_status="unreadable", quality=None,
                                                     quality_status="unreadable"))
        self.assertEqual((o.source_status, o.quality_status), ("unreadable", "unreadable"))

    def test_digest_is_stable(self):
        a = obs.parse_observation(self.cfg, self.obs())
        b = obs.parse_observation(self.cfg, dict(reversed(list(self.obs().items()))))
        c = obs.parse_observation(self.cfg, self.obs(value_text="1200"))
        self.assertEqual(a.digest, b.digest)
        self.assertNotEqual(a.digest, c.digest)

    def test_message_limits(self):
        cfg = config(limits=cfgmod.Limits(observations_per_message=2))
        with self.assertRaises(obs.ProtocolError):
            obs.parse_message(cfg, {"session": "s", "observations": [self.obs(seq=i) for i in range(3)]})
        with self.assertRaises(obs.ProtocolError):
            obs.parse_message(cfg, {"session": "s", "observations": [self.obs(seq=1), self.obs(seq=1)]})
        with self.assertRaises(obs.ProtocolError):
            obs.parse_message(cfg, {"session": "", "observations": [self.obs()]})

    def test_investigation_requests(self):
        ok = {"request_id": "8f2b5c1e-34d2-4a8e-9a43-3b8f7c1d2e10", "slot": "panel", "collection": "plant.rig",
              "recording_id": "r", "start_sample": 0, "end_sample": 4096}
        self.assertEqual(parse_request(self.cfg, ok)["top_k"], 10)
        for change in ({"request_id": "x"}, {"request_id": ok["request_id"].upper()}, {"slot": ""},
                       {"end_sample": 100}, {"end_sample": 40000}, {"top_k": 51}, {"top_k": 0}, {"start_sample": -1},
                       {"more": 1}):
            with self.assertRaises(obs.ProtocolError, msg=str(change)):
                parse_request(self.cfg, {**ok, **change})
        with self.assertRaises(obs.ForbiddenName):
            parse_request(self.cfg, {**ok, "collection": "other.rig"})


def sent_size(body) -> int:
    """Bytes of a body as the HTTP handler serializes it (server.Handler._send)."""
    return len(json.dumps(body, default=str, separators=(",", ":")).encode())


RID = "8f2b5c1e-34d2-4a8e-9a43-3b8f7c1d2e10"
OTHER = "0b7e9d4a-1c2f-4e3a-8b5d-6f7a8b9c0d1e"


def search_result(context=(), matches=(), recording_id="r1", status=None):
    """A result shaped like run_search's."""
    return {"source": {"collection": "plant.rig", "recording_id": recording_id, "start_sample": 0,
                       "end_sample": 4096, "context": list(context), "start_utc": None},
            "readiness": {"window": 4096, "source_searchable": True, "source_states": ["searchable"],
                          "searchable_windows": 96, "searchable_recordings": 4, "not_searched_samples": {}},
            "search_mode": "exact", "status": status or ("ok" if matches else "no_matches"),
            "matches": list(matches), "note": "distances are envelope distances"}


def match(rank):
    return {"rank": rank, "recording_id": f"rec-{rank}", "start_sample": 2048 * rank,
            "end_sample": 2048 * rank + 4096, "distance": 0.01 * rank, "start_utc": None, "end_utc": None,
            "overview": [[0, 256, -1.0, 1.0, 0.5]] * 16, "context": [],
            "labels": [{"label": "impact", "start_sample": 0, "end_sample": 4096, "note": None}],
            "viewer_url": None}


def row(key, value, start=0):
    return {"key": key, "value": value, "start_sample": start, "end_sample": None}


class ResponseBoundTests(unittest.TestCase):
    """#497: response_bytes bounds the body actually sent, source context and metadata included."""

    def finished(self, result, limit=256 * 1024, slot="panel", superseded=False, current=True):
        cfg = config(limits=dataclasses.replace(cfgmod.Limits(), response_bytes=limit))
        invs = inv.Investigations(cfg, connect=None)
        r = inv.Request(RID, slot, "d", {})
        invs.requests[RID] = r
        if current:
            invs.current[slot] = RID
        if superseded:
            r.superseded_by = OTHER
        invs._finish(r, copy.deepcopy(result))
        return invs.view(r)

    def test_issue_reproduction_source_context_alone(self):
        v = self.finished(search_result([row("description", "x" * 300_000)]))
        self.assertLessEqual(sent_size(v), 262_144)
        self.assertEqual(v["status"], "no_matches")
        ctx = v["source"]["context"]
        self.assertEqual(len(ctx), 1)
        self.assertEqual(ctx[0]["value"], "x" * inv.CONTEXT_VALUE_CHARS)
        self.assertEqual(ctx[0]["value_chars"], 300_000)
        self.assertEqual(v["truncated"], ["source_context_values"])
        self.assertEqual(v["omitted"], {"source_context_values": 1, "source_context": 0, "matches": 0})

    def test_matches_are_kept_over_oversized_source_context(self):
        # One huge value: cutting it is enough, and the matches keep all their evidence.
        res = search_result([row("description", "x" * 300_000)], [match(i) for i in range(1, 11)])
        v = self.finished(res)
        self.assertLessEqual(sent_size(v), 262_144)
        self.assertEqual(v["matches"], res["matches"])
        self.assertEqual(v["truncated"], ["source_context_values"])
        # Many context rows: the trailing ones (in key, start order) go before any match does.
        rows = [row(f"k{i:05d}", "v" * 200, i) for i in range(3000)]
        res = search_result(rows, [match(i) for i in range(1, 11)])
        v = self.finished(res)
        self.assertLessEqual(sent_size(v), 262_144)
        self.assertEqual([m["rank"] for m in v["matches"]], list(range(1, 11)))
        kept = v["source"]["context"]
        self.assertGreater(len(kept), 0)
        self.assertEqual(kept, rows[:len(kept)])
        self.assertEqual(v["omitted"]["source_context"], 3000 - len(kept))
        self.assertEqual(v["omitted"]["matches"], 0)
        self.assertIn("source_context", v["truncated"])
        self.assertNotIn("matches", v["truncated"])
        self.assertEqual(self.finished(res), v)                 # deterministic

    def test_final_body_with_view_and_truncation_metadata_fits(self):
        limit = 4096
        head = {"schema": "morlet-winccoa/1", "request_id": RID, "slot": "panel"}
        # Pad a match's context so the stored result alone is just at or below the limit, before the truncation
        # report and the view metadata (state, current, superseded_by) are added; measured both compact (as sent)
        # and with json.dumps' default separators.
        for measure in (sent_size, lambda b: len(json.dumps(b, default=str).encode())):
            for deficit in range(0, 160, 3):
                res = search_result([], [{**match(1), "overview": [], "labels": [], "context": [row("pad", "")]}])
                res["matches"][0]["context"][0]["value"] = "p" * (limit - deficit - measure({**head, **res}))
                self.assertEqual(measure({**head, **res}), limit - deficit)
                self.check_just_below(res, limit, deficit)
        # A result that fits with the largest view metadata is returned unchanged.
        base = search_result([row("k", "v" * 1000)] * 2, [match(1)])
        v = self.finished(base, limit=sent_size({**head, **base}) + 200, superseded=True)
        self.assertNotIn("truncated", v)
        self.assertEqual(v["matches"], base["matches"])

    def check_just_below(self, res, limit, deficit):
        for superseded in (False, True):
            v = self.finished(res, limit=limit, superseded=superseded, current=not superseded)
            self.assertLessEqual(sent_size(v), limit, (deficit, superseded))
            self.assertEqual((v["status"], len(v["matches"])), ("ok", 1))

    def test_trailing_matches_go_last(self):
        res = search_result([row("k", "v" * 3000)], [match(i) for i in range(1, 51)])
        v = self.finished(res, limit=6000)
        self.assertLessEqual(sent_size(v), 6000)
        ranks = [m["rank"] for m in v["matches"]]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))
        self.assertEqual(v["omitted"]["matches"], 50 - len(ranks))
        self.assertEqual(v["source"]["context"], [])
        self.assertEqual(v["truncated"], ["source_context_values", "context", "labels", "overview",
                                          "source_context", "matches"])

    def test_envelope_that_cannot_fit_is_an_explicit_error(self):
        v = self.finished(search_result([], [match(1)], recording_id="r" * 10_000), limit=4096)
        self.assertLessEqual(sent_size(v), 4096)
        self.assertEqual(v["status"], "response_too_large")
        self.assertEqual(v["result_status"], "ok")
        self.assertEqual(v["response_bytes"], 4096)
        self.assertNotIn("matches", v)
        self.assertEqual((v["request_id"], v["state"], v["current"]), (RID, "done", True))
        # The error itself fits the smallest permitted limit, with the longest slot (escaped) and a superseder.
        v = self.finished(search_result(recording_id="r" * 10_000), limit=cfgmod.MIN_RESPONSE_BYTES,
                          slot=" " * 100, superseded=True, current=False)
        self.assertLessEqual(sent_size(v), cfgmod.MIN_RESPONSE_BYTES)
        self.assertEqual(v["status"], "response_too_large")
        with self.assertRaises(cfgmod.ConfigError):
            config(limits=dataclasses.replace(cfgmod.Limits(), response_bytes=cfgmod.MIN_RESPONSE_BYTES - 1))

    def test_final_http_body(self):
        """POST and GET over the real HTTP handler: the body received is within the limit."""
        from adapter_sim import Client
        from morlet_winccoa.server import Companion, make_server

        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        token = secrets.token_hex(32)
        path = os.path.join(d, "token")
        with open(path, "w") as f:
            f.write(token + "\n")
        os.chmod(path, 0o600)
        limit = 8192
        cfg = config(token_file=path, listen_port=0,
                     limits=dataclasses.replace(cfgmod.Limits(), response_bytes=limit))

        class Conn:
            """A connection that is never queried: run_search is replaced below."""
            info = type("info", (), {"backend_pid": 1})()

            def execute(self, *a):
                return self

            def commit(self):
                pass

            rollback = close = commit

        rows = [row(f"k{i:04d}", "v" * 100, i) for i in range(400)] + [row("zz", "x" * 300_000)]
        res = search_result(rows, [match(i) for i in range(1, 11)])
        orig = inv.run_search
        inv.run_search = lambda conn, cfg, req: copy.deepcopy(res)
        self.addCleanup(setattr, inv, "run_search", orig)
        companion = Companion(cfg, connect=Conn)
        srv = make_server(companion)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        c = Client("127.0.0.1", srv.server_address[1], token)
        body = {"schema": "morlet-winccoa/1", "request_id": RID, "slot": "panel", "collection": "plant.rig",
                "recording_id": "r1", "start_sample": 0, "end_sample": 4096}
        for r in (c.request("POST", "/v1/investigations", body), c.request("GET", f"/v1/investigations/{RID}")):
            self.assertEqual(r.status, 200, r.body)
            self.assertLessEqual(r.size, limit)
            self.assertEqual(r.body["status"], "ok")
            self.assertEqual(len(r.body["matches"]), 10)
            self.assertIn("source_context", r.body["truncated"])
        # A new request on the slot supersedes the first: its stored result gains view metadata and still fits.
        c.request("POST", "/v1/investigations", {**body, "request_id": OTHER})
        r = c.request("GET", f"/v1/investigations/{RID}")
        self.assertEqual((r.body["superseded_by"], r.body["current"]), (OTHER, False))
        self.assertLessEqual(r.size, limit)


if __name__ == "__main__":
    unittest.main()
