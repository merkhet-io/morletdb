# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""Row-level security: the companion tables must not bypass the collection's access control.

Two tenants share one collection; the recordings table's own policy shows each login role only its site's
recordings, and `morlet_enable_row_security` makes the core's companion tables follow. The companion's tables follow
the acquisition. Checked for both enablement orders (companion schema before and after morlet_enable_row_security),
for direct SQL on every companion table and through the service.
"""
import dataclasses
import time
import unittest
import uuid

import psycopg

import support
from machine import current
from morlet_winccoa import config as cfgmod
from morlet_winccoa.observations import mark_finished
from morlet_winccoa.server import Companion, make_server
from adapter_sim import Adapter, Client
from support import SPEED
from writer import recording_id, write_acquisition

GOOD = {"good": True}


class Tenants:
    """Mixin: set up two tenants in the order given by `companion_first`."""
    companion_first = True

    def setUp(self):
        db = support.database()
        self.env = env = support.Env(db, install=False)
        self.addCleanup(env.drop)
        self.login_b = support.new_role(db, "wc_login_b")
        self.addCleanup(self._drop, self.login_b)
        with env.writer() as w:
            w.execute(f"ALTER TABLE {env.collection} ENABLE ROW LEVEL SECURITY")
            w.execute(f"CREATE POLICY site ON {env.collection} USING (metadata->>'site' = current_user)")
            w.commit()
        if self.companion_first:
            env.install(env.login)
            env.install(self.login_b)
            self._enable_rls()
        else:
            self._enable_rls()
            env.install(env.login)
            env.install(self.login_b)
        self.plans = {}
        for tenant, login in (("a", env.login), ("b", self.login_b)):
            plan = dataclasses.replace(current(), acquisition_id=f"acq-{tenant}-{uuid.uuid4().hex[:6]}")
            origin = (time.time_ns() // 1000) * 1000 - plan.end_ns
            with env.writer() as w:
                write_acquisition(w, env.collection, plan, origin, metadata={"site": login})
            self.plans[tenant] = (plan, origin)
        self.cfg_b = cfgmod.with_changes(env.cfg, dsn=db.dsn(self.login_b))
        self.ad_a = env.start()
        self.ad_b = self._start(self.cfg_b)

    def _enable_rls(self):
        with self.env.writer() as w:
            w.execute(f"SELECT morlet_enable_row_security('{self.env.collection}'::regclass)")
            w.commit()

    def _start(self, cfg):
        companion = Companion(cfg)
        srv = make_server(companion)
        import threading
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close(), companion.stop()))
        return Adapter(Client("127.0.0.1", srv.server_address[1], self.env.token))

    def _drop(self, role):
        with self.env.db.admin(autocommit=True) as c:
            c.execute(f'DROP OWNED BY "{role}"')
            c.execute(f'DROP ROLE "{role}"')

    def as_role(self, login):
        return psycopg.connect(self.env.db.dsn(login), autocommit=True)

    def send(self, adapter, tenant, other=False):
        plan, origin = self.plans[tenant]
        adapter.heartbeat(plan.acquisition_id, "rig-1")
        adapter.change(plan.acquisition_id, SPEED, "1800", "int", origin, quality=GOOD)
        adapter.change(plan.acquisition_id, SPEED, "1200", "int", origin + 5_000_000_000, quality=GOOD)
        return adapter.flush()[-1]

    def test_tenants_see_and_write_only_their_own(self):
        env, schema = self.env, self.env.cfg.schema
        self.assertEqual(self.send(self.ad_a, "a").status, 200)
        self.assertEqual(self.send(self.ad_b, "b").status, 200)
        # Through the service: an acquisition of the other tenant is not visible, so nothing is stored.
        self.ad_a.unacked.clear()
        r = self.send(self.ad_a, "b")
        self.assertEqual(r.status, 422)
        self.ad_a.unacked.clear()
        with env.writer() as w:
            for t in ("a", "b"):
                mark_finished(w, env.cfg, env.collection, self.plans[t][0].acquisition_id, self.plans[t][0].end_ns)
            w.commit()
        from morlet_winccoa import derive
        with self.as_role(env.login) as c:
            sa = derive.derive_all(c, env.cfg)
        with self.as_role(self.login_b) as c:
            sb = derive.derive_all(c, self.cfg_b)
        self.assertEqual([s.acquisition_id for s in sa], [self.plans["a"][0].acquisition_id])
        self.assertEqual([s.acquisition_id for s in sb], [self.plans["b"][0].acquisition_id])
        acq_a, acq_b = self.plans["a"][0].acquisition_id, self.plans["b"][0].acquisition_id
        with self.as_role(env.login) as c:
            for table in ("observation", "derivation", "acquisition_end"):
                ids = {r[0] for r in c.execute(f"SELECT DISTINCT acquisition_id FROM {schema}.{table}").fetchall()}
                self.assertEqual(ids, {acq_a}, table)
            ctx = {r[0] for r in c.execute(f"SELECT DISTINCT recording_id FROM {env.collection}_ctx").fetchall()}
            self.assertEqual(ctx, {recording_id(self.plans["a"][0], r) for r in ("vib", "current")})
        with env.writer() as w:      # the owner sees both
            b_obs = w.execute(f"SELECT obs_id FROM {schema}.observation WHERE acquisition_id = %s LIMIT 1",
                              (acq_b,)).fetchone()[0]
            self.assertEqual(w.execute(f"SELECT count(DISTINCT acquisition_id) FROM {schema}.observation").fetchone()[0], 2)
        # Direct writes naming the other tenant's acquisition are refused by the policies.
        writes = [
            (f"INSERT INTO {schema}.observation (collection, acquisition_id, asset, kind, source_status, quality_status,"
             " observed_ns, received_ns, adapter_session, seq, digest) VALUES (%s, %s, 'rig-1', 'heartbeat', 'missing',"
             " 'missing', 1, 1, 'x', 1, 'd')", (env.collection, acq_b)),
            (f"INSERT INTO {schema}.derivation (obs_id, collection, acquisition_id, key, reason, time_basis) "
             "VALUES (%s, %s, %s, 'speed', 'open', 'source')", (b_obs, env.collection, acq_b)),
            (f"INSERT INTO {schema}.acquisition_end (collection, acquisition_id, end_ns) VALUES (%s, %s, 1)",
             (env.collection, acq_b + "x")),
        ]
        with self.as_role(env.login) as c:
            for q, args in writes:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege, msg=q):
                    c.execute(q, args)
            # Append-only: no updates or deletes of observations, even of its own.
            for q in (f"UPDATE {schema}.observation SET value_text = 'x'", f"DELETE FROM {schema}.observation",
                      f"DELETE FROM {schema}.derivation"):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege, msg=q):
                    c.execute(q)
            # Updating a visible derivation to point at the other tenant's acquisition fails the check.
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"UPDATE {schema}.derivation SET acquisition_id = %s", (acq_b,))
        # Search follows the recordings' policy: the other tenant's recordings are neither results nor sources.
        plan_a, plan_b = self.plans["a"][0], self.plans["b"][0]
        impact = next(e for e in plan_a.events if e.label == "impact")
        body = self.ad_a.start_investigation("p", env.collection, recording_id(plan_a, "vib"), impact.vib_start,
                                             impact.vib_start + 4096)
        r = self.ad_a.send_investigation(body)
        self.assertEqual(r.body["status"], "ok", r.body)
        self.assertTrue(r.body["matches"])
        self.assertTrue(all(m["recording_id"] == recording_id(plan_a, "vib") for m in r.body["matches"]))
        self.assertEqual(r.body["readiness"]["searchable_recordings"], 2)        # its own vib and current only
        body = self.ad_a.start_investigation("p", env.collection, recording_id(plan_b, "vib"), impact.vib_start,
                                             impact.vib_start + 4096)
        r = self.ad_a.send_investigation(body)
        self.assertEqual(r.body["status"], "evidence_unavailable")
        body = self.ad_b.start_investigation("p", env.collection, recording_id(plan_b, "vib"), impact.vib_start,
                                             impact.vib_start + 4096)
        r = self.ad_b.send_investigation(body)
        self.assertTrue(all(m["recording_id"].startswith(plan_b.acquisition_id) for m in r.body.get("matches", [])))

    def test_role_without_grants_is_denied(self):
        other = support.new_role(self.env.db, "wc_nobody")
        self.addCleanup(self._drop, other)
        with self.as_role(other) as c:
            for table in ("observation", "derivation", "acquisition_end"):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    c.execute(f"SELECT 1 FROM {self.env.cfg.schema}.{table}")


class CompanionFirst(Tenants, unittest.TestCase):
    companion_first = True


class RowSecurityFirst(Tenants, unittest.TestCase):
    companion_first = False


if __name__ == "__main__":
    unittest.main()
