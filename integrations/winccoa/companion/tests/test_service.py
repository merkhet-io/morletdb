# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""The HTTP service against a real MorletDB: authentication, malformed requests, investigation statuses, request
correlation (retries, superseded and late responses, cancellation), limits and an unavailable database."""
import dataclasses
import json
import logging
import os
import threading
import time
import unittest
import uuid

import morletdb
import numpy as np

import support
from adapter_sim import SCHEMA, Adapter, Client
from machine import history
from morlet_winccoa import config as cfgmod
from morlet_winccoa.server import Companion, make_server
from replay import write_history
from writer import recording_id

ENV = None
SOURCE = None     # a historical impact window: (recording_id, start, end)


def setUpModule():
    global ENV, SOURCE
    ENV = support.Env(support.database())
    with ENV.writer() as w:
        write_history(w, ENV.collection)
        db = morletdb.Client(w)
        # A recording that is the only one of its kind: its own window is excluded, so nothing can match.
        db.ingest(ENV.collection, "solo", np.sin(np.arange(4096) / 7.0), sample_rate_hz=8192, channel="solo_probe",
                  unit="g")
        w.commit()
    p = history()[0]
    e = next(e for e in p.events if e.label == "impact")
    k = e.vib_start - e.vib_start % 2048
    SOURCE = (recording_id(p, "vib"), k, k + 4096)


def tearDownModule():
    ENV.drop()


class Service(unittest.TestCase):
    def start(self, **changes):
        cfg = ENV.cfg
        lim = {k: v for k, v in changes.items() if k in {f.name for f in dataclasses.fields(cfgmod.Limits)}}
        rest = {k: v for k, v in changes.items() if k not in lim}
        if lim:
            rest["limits"] = dataclasses.replace(cfg.limits, **lim)
        cfg = cfgmod.with_changes(cfg, **rest) if rest else cfg
        companion = Companion(cfg)
        srv = make_server(companion)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close(), companion.stop()))
        return companion, Adapter(Client("127.0.0.1", srv.server_address[1], ENV.token))

    def investigation(self, adapter, slot="panel", source=None, **kw):
        rid, a, b = source or SOURCE
        return adapter.start_investigation(slot, ENV.collection, rid, a, b, **kw)

    def wait_for_lock_waiters(self, n, user=None):
        deadline = time.monotonic() + 10
        with ENV.db.admin(autocommit=True) as c:
            while time.monotonic() < deadline:
                k = c.execute("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND usename = %s",
                              (user or ENV.login,)).fetchone()[0]
                if k >= n:
                    return
                time.sleep(0.05)
        self.fail(f"{n} investigations did not start waiting")

    def lock_envelopes(self):
        c = ENV.db.admin()
        c.execute(f"LOCK TABLE {ENV.collection}_envelopes IN ACCESS EXCLUSIVE MODE")
        self.addCleanup(c.close)
        return c


class Authentication(Service):
    def test_missing_wrong_and_rotated_tokens(self):
        _, ad = self.start()
        c = ad.client
        self.assertEqual(Client(c.host, c.port, "").request("GET", "/v1/health").status, 401)
        wrong = Client(c.host, c.port, os.urandom(32).hex())
        with self.assertLogs("morlet_winccoa", level=logging.INFO) as logs:
            self.assertEqual(wrong.request("GET", "/v1/health").status, 401)
            self.assertEqual(c.request("GET", "/v1/health").status, 200)
        self.assertFalse(any(wrong.token in line or ENV.token in line for line in logs.output))
        # Rotation: add the next token, then remove the old one.
        nxt = os.urandom(32).hex()
        with open(ENV.token_file, "w") as f:
            f.write(f"{ENV.token}\n{nxt}\n")
        os.utime(ENV.token_file, ns=(time.time_ns(), time.time_ns() + 1_000_000_000))
        self.assertEqual(Client(c.host, c.port, nxt).request("GET", "/v1/health").status, 200)
        self.assertEqual(c.request("GET", "/v1/health").status, 200)
        with open(ENV.token_file, "w") as f:
            f.write(f"{ENV.token}\n")
        os.utime(ENV.token_file, ns=(time.time_ns(), time.time_ns() + 2_000_000_000))
        self.assertEqual(Client(c.host, c.port, nxt).request("GET", "/v1/health").status, 401)

    def test_backoff_after_repeated_failures(self):
        _, ad = self.start()
        wrong = Client(ad.client.host, ad.client.port, os.urandom(32).hex())
        for _ in range(5):
            self.assertEqual(wrong.request("GET", "/v1/health").status, 401)
        r = ad.client.request("GET", "/v1/health")           # even the right token, from the same address
        self.assertEqual(r.status, 429)
        self.assertEqual(r.body["error"], "authentication_backoff")

    def test_refuses_to_start_with_an_open_token_file(self):
        os.chmod(ENV.token_file, 0o644)
        try:
            from morlet_winccoa.auth import TokenFileError
            with self.assertRaises(TokenFileError):
                Companion(ENV.cfg)
        finally:
            os.chmod(ENV.token_file, 0o600)

    def test_wrong_host_header(self):
        _, ad = self.start()
        r = ad.client.request("GET", "/v1/health", headers={"Host": "attacker.example:8770"})
        self.assertEqual(r.status, 421)


class Malformed(Service):
    def test_malformed_requests(self):
        _, ad = self.start(body_bytes=2000)
        c = ad.client
        self.assertEqual(c.request("POST", "/v1/observations", raw=b"{not json").status, 400)
        r = c.request("POST", "/v1/observations", {"schema": "morlet-winccoa/2", "session": "s", "observations": []})
        self.assertEqual((r.status, r.body["supported"]), (400, [SCHEMA]))
        self.assertEqual(c.request("POST", "/v1/observations", {"session": "s"}).status, 400)
        self.assertEqual(c.request("POST", "/v1/observations", raw=b"{}", headers={"Content-Type": "text/plain"}).status, 415)
        big = {"schema": SCHEMA, "session": "s", "observations": [{"seq": 1, "pad": "x" * 3000}]}
        self.assertEqual(c.request("POST", "/v1/observations", big).status, 413)
        self.assertEqual(c.request("POST", "/v1/observations", {"schema": SCHEMA, "session": "s",
                                                                 "observations": []}).status, 400)
        self.assertEqual(c.request("POST", "/v1/observations", raw=b'{"schema":"morlet-winccoa/1","x":NaN}').status, 400)
        self.assertEqual(c.request("POST", "/v1/nothing", {"schema": SCHEMA}).status, 404)
        self.assertEqual(c.request("GET", "/v1/investigations/" + str(uuid.uuid4())).status, 404)
        self.assertEqual(c.request("PUT", "/v1/observations", {"schema": SCHEMA}).status, 405)
        body = self.investigation(ad)
        body["end_sample"] = body["start_sample"] + 100
        self.assertEqual(c.request("POST", "/v1/investigations", body).status, 400)
        body = self.investigation(ad)
        body["collection"] = "public.other"
        self.assertEqual(c.request("POST", "/v1/investigations", body).status, 403)
        # Nothing was stored by any of them.
        with ENV.writer() as w:
            self.assertEqual(w.execute(f"SELECT count(*) FROM {ENV.cfg.schema}.observation WHERE adapter_session = 's'"
                                       ).fetchone()[0], 0)


class Statuses(Service):
    def test_ok_with_mode_readiness_and_viewer_links(self):
        _, ad = self.start(viewer_base_url="http://127.0.0.1:8765")
        r = ad.send_investigation(self.investigation(ad))
        self.assertEqual((r.status, r.body["status"], r.body["search_mode"]), (200, "ok", "exact"))
        self.assertTrue(r.body["readiness"]["source_searchable"])
        self.assertEqual(r.body["readiness"]["window"], 4096)
        m = r.body["matches"][0]
        self.assertEqual(m["viewer_url"], f"http://127.0.0.1:8765/?collection={ENV.collection}"
                         f"&recording={m['recording_id']}&start={m['start_sample']}&end={m['end_sample']}")
        self.assertEqual(len(r.body["matches"]), 10)

    def test_no_matches_reports_what_was_searched(self):
        _, ad = self.start()
        r = ad.send_investigation(self.investigation(ad, source=("solo", 0, 4096)))
        self.assertEqual((r.status, r.body["status"], r.body["matches"]), (200, "no_matches", []))
        self.assertIn("readiness", r.body)

    def test_evidence_unavailable(self):
        _, ad = self.start()
        rid = SOURCE[0]
        for src, why in (((rid, 196_608 - 1024, 196_608 + 3072), "ends at"),            # not stored
                         ((rid, 0, 2048), "no search window of 2048"),                     # no windows of this size
                         (("no-such-recording", 0, 4096), "does not exist or is not visible")):
            r = ad.send_investigation(self.investigation(ad, source=src))
            self.assertEqual((r.status, r.body["status"]), (200, "evidence_unavailable"), r.body)
            self.assertIn(why, r.body["detail"])
        # A tail that is stored but not in any built window yet.
        with ENV.writer() as w:
            morletdb.Client(w).ingest(ENV.collection, "tail", np.cos(np.arange(6000) / 5.0), sample_rate_hz=8192,
                                      channel="housing_accel", unit="g")
            w.commit()
        r = ad.send_investigation(self.investigation(ad, source=("tail", 1904, 6000)))
        self.assertEqual(r.body["status"], "evidence_unavailable")
        self.assertIn("not_indexed", r.body["readiness"]["source_states"])

    def test_permission_denied(self):
        login = support.new_role(ENV.db, "wc_noread")
        with ENV.db.admin(autocommit=True) as c:
            c.execute(f'GRANT "{ENV.cfg.context_role}" TO "{login}"')       # writer grants only, no reader grants
        self.addCleanup(lambda: _drop_role(login))
        _, ad = self.start(dsn=ENV.db.dsn(login))
        r = ad.send_investigation(self.investigation(ad))
        self.assertEqual((r.status, r.body["status"]), (200, "permission_denied"), r.body)

    def test_response_size_limit(self):
        _, ad = self.start(response_bytes=4000)
        r = ad.send_investigation(self.investigation(ad))
        self.assertEqual(r.body["status"], "ok")
        self.assertLessEqual(len(json.dumps(r.body).encode()), 4000 + 200)
        self.assertIn("context", r.body["truncated"])


class Correlation(Service):
    def test_identical_retry_conflict_and_lookup(self):
        _, ad = self.start()
        body = self.investigation(ad)
        a = ad.send_investigation(body)
        b = ad.send_investigation(body)
        self.assertEqual(a.body["request_id"], body["request_id"])
        self.assertEqual(a.body["matches"], b.body["matches"])
        changed = dict(body, top_k=3)
        self.assertEqual(ad.send_investigation(changed).status, 409)
        g = ad.client.request("GET", f"/v1/investigations/{body['request_id']}")
        self.assertEqual((g.status, g.body["state"], g.body["current"]), (200, "done", True))
        self.assertEqual(g.body["matches"], a.body["matches"])

    def test_superseded_request_is_cancelled_and_its_late_response_dropped(self):
        companion, ad = self.start()
        lock = self.lock_envelopes()
        first = self.investigation(ad, slot="panel-1")
        out = {}
        t1 = threading.Thread(target=lambda: out.__setitem__("first", ad.send_investigation(first)))
        t1.start()
        self.wait_for_lock_waiters(1)
        second = self.investigation(ad, slot="panel-1")         # the operator picked another interval
        t2 = threading.Thread(target=lambda: out.__setitem__("second", ad.send_investigation(second)))
        t2.start()
        t1.join(10)
        self.assertFalse(t1.is_alive(), "the superseded query was not cancelled")
        late = out["first"]
        self.assertEqual((late.body["status"], late.body["superseded_by"], late.body["current"]),
                         ("service_failure", second["request_id"], False))
        self.assertIn("superseded", late.body["detail"])
        self.assertFalse(ad.accept(late))                       # the adapter drops the late result
        self.assertEqual(ad.late_dropped, 1)
        lock.rollback()
        t2.join(15)
        self.assertEqual(out["second"].body["status"], "ok")
        self.assertTrue(ad.accept(out["second"]))
        self.assertEqual(ad.results["panel-1"]["request_id"], second["request_id"])
        g = ad.client.request("GET", f"/v1/investigations/{first['request_id']}").body
        self.assertEqual((g["current"], g["superseded_by"]), (False, second["request_id"]))
        self.assertEqual(companion.investigations.stats()["superseded"], 1)

    def test_late_response_after_completion(self):
        _, ad = self.start()
        first = self.investigation(ad, slot="p")
        r1 = ad.send_investigation(first)
        second = self.investigation(ad, slot="p")
        r2 = ad.send_investigation(second)
        self.assertTrue(ad.accept(r2))
        self.assertFalse(ad.accept(r1))         # an old request_id arriving after the new one: dropped
        self.assertEqual(ad.results["p"]["request_id"], second["request_id"])

    def test_concurrency_limit_and_deadline(self):
        _, ad = self.start(investigations_total=2, http_deadline_ms=500)
        lock = self.lock_envelopes()
        a, b = self.investigation(ad, slot="s1"), self.investigation(ad, slot="s2")
        ra, rb = ad.send_investigation(a), ad.send_investigation(b)
        self.assertEqual((ra.status, ra.body["state"], rb.status), (202, "running", 202))
        r = ad.send_investigation(self.investigation(ad, slot="s3"))
        self.assertEqual((r.status, r.body["error"]), (429, "busy"))
        lock.rollback()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            g = ad.client.request("GET", f"/v1/investigations/{a['request_id']}").body
            if g["state"] == "done":
                break
            time.sleep(0.1)
        self.assertEqual((g["state"], g["status"]), ("done", "ok"))

    def test_statement_timeout(self):
        _, ad = self.start(statement_timeout_ms=300)
        self.lock_envelopes()
        r = ad.send_investigation(self.investigation(ad))
        self.assertEqual((r.body["status"], r.body["detail"]), ("service_failure", "statement timeout exceeded"))


class Unavailable(Service):
    def test_database_unavailable_then_recovered(self):
        companion, ad = self.start()
        p = history()[0]
        ad.heartbeat(p.acquisition_id, "rig-1")
        ad.change(p.acquisition_id, support.SPEED, "1800", "int", 1_790_812_800_000_000_000, quality={"good": True})
        ENV.db.pause()
        try:
            rs = ad.flush()
            self.assertEqual(rs[-1].status, 503)
            self.assertEqual(len(ad.unacked), 2)                 # not acknowledged: kept for resending
            r = ad.send_investigation(self.investigation(ad))
            self.assertEqual((r.status, r.body["status"]), (503, "service_failure"))
            self.assertEqual(ad.client.request("GET", "/v1/health").status, 503)
        finally:
            ENV.db.unpause()
        rs = ad.flush()
        self.assertEqual((rs[-1].status, rs[-1].body["stored"]), (200, 2))
        self.assertEqual(ad.unacked, [])
        self.assertEqual(ad.send_investigation(self.investigation(ad)).body["status"], "ok")
        with ENV.writer() as w:
            n = w.execute(f"SELECT count(*) FROM {ENV.cfg.schema}.observation WHERE adapter_session = %s",
                          (ad.session,)).fetchone()[0]
        self.assertEqual(n, 2)


def _drop_role(role):
    with ENV.db.admin(autocommit=True) as c:
        c.execute(f'DROP OWNED BY "{role}"')
        c.execute(f'DROP ROLE "{role}"')


if __name__ == "__main__":
    unittest.main()
