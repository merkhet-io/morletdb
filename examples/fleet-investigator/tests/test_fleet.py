# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""Fleet investigator, end to end against a disposable demo database loaded with the real IMS recordings.

  MORLET_DATA=/path/to/ims python -m unittest discover -s examples/fleet-investigator/tests -v

Covers: the loader (idempotent, manifest, samples bit for bit), every graph hop against direct SQL on the label
tables, time-bounded install resolution (with the mid-history replacement), the flagship investigation against the
IMS readme's outcomes, the access rule for the contractor in both enablement orders, the atomic write-back, one
snapshot across graph and recordings, and that no test container is left behind.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import numpy as np
import psycopg

import support

DEMO = support.DEMO
sys.path.insert(0, str(DEMO))
import checks  # noqa: E402
import confirm_finding  # noqa: E402
import fleetdb  # noqa: E402
import investigate  # noqa: E402
import load  # noqa: E402
import plant  # noqa: E402
import queries  # noqa: E402

# Held-out truth: the IMS readme (Readme Document for IMS Bearing Data.pdf, in the NASA archive), set no. 3: "At the
# end of the test-to-failure experiment, outer race failure occurred in bearing 3." Not in the graph (it happens after
# the plant's NOW); the investigation must not read it, only be checked against it.
HELD_OUT_TEST3_BEARING3 = "FM-OR"


def setUpModule():
    support.database()


def tearDownModule():
    if support._db is not None:
        support._db.remove()
    left = support.leftover_containers()
    if left:
        raise AssertionError(f"test containers left behind: {left}")


def db():
    return support.database().db


def fingerprint(c):
    """Everything the loader writes, as counts and digests."""
    out = {}
    for t in ("", "_chunks", "_envelopes", "_ctx", "_segments", "_labels", "_episodes", "_label_definitions"):
        out[t or "recordings"] = c.execute(f"SELECT count(*) FROM {plant.COLLECTION}{t}").fetchone()[0]
    out["recordings_md5"] = c.execute(f"SELECT md5(string_agg(recording_id || checkpoint::text || metadata::text, "
                                      f"',' ORDER BY recording_id)) FROM {plant.COLLECTION}").fetchone()[0]
    out["chunks_md5"] = c.execute(f"SELECT md5(string_agg(md5(c::text), ',' ORDER BY c::text)) "
                                  f"FROM {plant.COLLECTION}_chunks c").fetchone()[0]
    out["graph_md5"] = c.execute("SELECT md5(string_agg(id::text || properties::text, ',' ORDER BY id)) "
                                 "FROM plant._ag_label_vertex").fetchone()[0]
    out["edges_md5"] = c.execute("SELECT md5(string_agg(id::text || start_id::text || end_id::text || "
                                 "properties::text, ',' ORDER BY id)) FROM plant._ag_label_edge").fetchone()[0]
    return out


class Test1Load(unittest.TestCase):
    def test_counts(self):
        chosen = load.selection(load.runs_root(support.data_dir()))
        self.assertEqual({t: len(n) for t, n in chosen.items()}, {"1": 360, "2": 164, "3": 1040})
        vertices, edges = load.graph_rows(chosen)
        with db().connect() as c:
            fleetdb.set_search_path(c)
            self.assertEqual(c.execute(f"SELECT count(*) FROM {plant.COLLECTION}").fetchone()[0], 6256)
            self.assertEqual(c.execute(f"SELECT count(*) FROM {plant.COLLECTION}_envelopes").fetchone()[0], 6256 * 9)
            self.assertEqual(c.execute("SELECT count(*) FROM plant._ag_label_vertex").fetchone()[0],
                             sum(len(v) for v in vertices.values()))
            self.assertEqual(c.execute("SELECT count(*) FROM plant._ag_label_edge").fetchone()[0],
                             sum(len(v) for v in edges.values()))
            self.assertEqual(c.execute("SELECT * FROM fleet.graph_security_gaps('plant')").fetchall(), [])
            self.assertTrue(c.execute("SELECT morlet_row_security_enabled(%s::regclass)",
                                      (plant.COLLECTION,)).fetchone()[0])

    def test_idempotent(self):
        with db().connect() as c:
            before = fingerprint(c)
        out = support.database().load()
        self.assertIn("all 6256 recordings are loaded", out)
        self.assertIn("already mirrored", out)
        self.assertIn("already written", out)
        with db().connect() as c:
            self.assertEqual(fingerprint(c), before)

    def test_samples_bit_exact(self):
        """First, middle and last snapshot of every IMS test, every position: stored samples equal the source
        file's column, byte for byte (float64), including the sign of zero."""
        root = load.runs_root(support.data_dir())
        chosen = load.selection(root)
        negzero = 0
        with db().connect(fleetdb.READER) as c:
            for test, names in chosen.items():
                for name in (names[0], names[len(names) // 2], names[-1]):
                    path = root / plant.IMS_RUNS[test]["folder"] / name
                    values = load.read_snapshot(path, test)
                    for i, col in enumerate(plant.IMS_RUNS[test]["use"]):
                        rid, meta = load.metadata_for(test, i, path)
                        raw = c.execute("SELECT morlet_read_interval_raw(%s::regclass, %s, 0, %s)",
                                        (plant.COLLECTION, rid, load.SAMPLES)).fetchone()[0]
                        want = np.ascontiguousarray(values[:, col], "<f8")
                        self.assertEqual(bytes(raw), want.tobytes(), rid)
                        negzero += int(np.sum(np.signbit(want) & (want == 0)))
                        stored = c.execute(f"SELECT metadata FROM {plant.COLLECTION} WHERE recording_id = %s",
                                           (rid,)).fetchone()[0]
                        self.assertEqual(stored["equipment_id"], meta["equipment_id"])
                        self.assertEqual(stored["source"], meta["source"])
        print(f"\n  bit-exact: 3 snapshots x 4 positions x 3 tests; {negzero} negative zeros among them")

    def test_manifest_mismatch_refused(self):
        root = load.runs_root(support.data_dir())
        chosen = load.selection(root)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for test, names in chosen.items():
                folder = tmp / plant.IMS_RUNS[test]["folder"]
                folder.mkdir(parents=True)
                for n in names:
                    (folder / n).symlink_to(root / plant.IMS_RUNS[test]["folder"] / n)
            victim = tmp / plant.IMS_RUNS["2"]["folder"] / chosen["2"][5]
            text = victim.read_text()
            victim.unlink()
            victim.write_text(text.replace("0.0", "0.1", 1))
            with self.assertRaises(SystemExit) as e:
                load.check_manifest(tmp, chosen)
            self.assertIn("1 files differ", str(e.exception))


def label_rows(c, label):
    return {r[0]: json.loads(r[1]) for r in c.execute(f'SELECT id::text, properties::text FROM plant."{label}"')}


def edge_rows(c, label):
    return [(r[0], r[1], json.loads(r[2])) for r in
            c.execute(f'SELECT start_id::text, end_id::text, properties::text FROM plant."{label}"')]


class GraphSQL:
    """The graph read with plain SQL on the label tables, independently of Cypher."""

    def __init__(self, c):
        self.v = {}
        for label in load.VERTEX_LABELS:
            for gid, p in label_rows(c, label).items():
                self.v[gid] = (label, p)
        self.e = {k: edge_rows(c, k) for k in load.EDGE_LABELS}
        self.by_id = {p["id"]: gid for gid, (_, p) in self.v.items()}

    def props(self, gid):
        return self.v[gid][1]

    def out(self, kind, src_id):
        g = self.by_id[src_id]
        return [(self.props(b), p) for a, b, p in self.e[kind] if a == g]

    def into(self, kind, dst_id):
        g = self.by_id[dst_id]
        return [(self.props(a), p) for a, b, p in self.e[kind] if b == g]


class Test2GraphHops(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = db().connect(fleetdb.READER)
        fleetdb.set_search_path(cls.conn)
        cls.g = GraphSQL(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_isa95_path(self):
        got = [p["id"] for p in queries.isa95_path(self.conn, "P-102.BRG-3")["path"]]
        want, node = ["P-102.BRG-3"], "P-102.BRG-3"
        while self.g.into("CONTAINS", node):
            node = self.g.into("CONTAINS", node)[0][0]["id"]
            want.append(node)
        self.assertEqual(got, want)
        self.assertEqual(got, ["P-102.BRG-3", "P-102.SHAFT", "P-102", "AW-N-A1-PS1", "AW-N-A1", "AW-N", "AW"])
        labels = [p["label"] for p in queries.isa95_path(self.conn, "P-102.BRG-3")["path"]]
        self.assertEqual(labels, ["EquipmentModule", "EquipmentModule", "Unit", "ProcessCell", "Area", "Site",
                                  "Enterprise"])

    def test_installed_at_matches_sql(self):
        for eq in ("P-102.BRG-1", "P-102.BRG-3", "F-212.BRG-4", "P-104.BRG-2"):
            for at in ("2003-11-01T00:00:00Z", "2004-02-15T00:00:00Z", "2004-02-25T00:00:00Z",
                       "2004-03-12T00:00:00Z", plant.NOW):
                got = queries.installed_at(self.conn, eq, at)["installed"]
                want = [a["id"] for a, p in self.g.into("INSTALLED_IN", eq)
                        if p["from"] <= at and ("to" not in p or at < p["to"])]
                self.assertEqual([got["asset"]] if got else [], want, (eq, at))

    def test_same_lot_matches_sql(self):
        got = queries.same_lot(self.conn, "SN-0402-007")
        lot = self.g.out("FROM_LOT", "SN-0402-007")[0][0]["id"]
        members = [a for a, _ in self.g.into("FROM_LOT", lot)]
        installed = sorted(a["id"] for a in members if a["id"] != "SN-0402-007" and any(
            "to" not in p for _, p in self.g.out("INSTALLED_IN", a["id"])))
        stored = sorted(a["id"] for a in members if self.g.out("STORED_AT", a["id"]))
        self.assertEqual(got["lot"]["id"], lot)
        self.assertEqual(sorted(x["asset"] for x in got["installed_elsewhere"]), installed)
        self.assertEqual(sorted(x["asset"] for x in got["in_stock"]), stored)
        self.assertEqual(sorted(x["equipment_id"] for x in got["installed_elsewhere"]),
                         ["F-113.BRG-1", "F-214.BRG-3", "P-102.BRG-1", "P-102.BRG-2", "P-102.BRG-4", "P-104.BRG-2",
                          "P-203.BRG-4"])

    def test_failure_history_matches_sql(self):
        got = queries.failure_history(self.conn, "P-102")
        positions = [m["id"] for m, _ in self.g.out("CONTAINS", "P-102.SHAFT")]
        assets = {a["id"] for p in positions for a, _ in self.g.into("INSTALLED_IN", p)}
        fails = sorted(f["id"] for a in assets for f, _ in self.g.into("OF_ASSET", a))
        self.assertEqual(sorted(f["id"] for f in got["failures"]), fails)
        self.assertEqual(fails, ["FL-2004-02-P102-BRG1"])
        f = got["failures"][0]
        self.assertEqual((f["mode"], f["mode_class"]), ("FM-OR", "Rolling bearing failure"))
        self.assertEqual(f["repair"][0]["work_order"]["id"],
                         self.g.out("RESPONDS_TO", self.g.into("FOUND", f["id"])[0][0]["id"])[0][0]["id"])
        self.assertEqual(sorted(w["work_order"]["id"] for w in got["work_orders"]),
                         sorted(w["id"] for w, _ in self.g.into("ON_UNIT", "P-102")))

    def test_spare_stock_matches_sql(self):
        for s in queries.spare_stock(self.conn):
            shelf = sorted(a["id"] for a, _ in self.g.into("STORED_AT", s["store"]))
            self.assertEqual(sorted(x["asset"] for x in s["serials"]), shelf)
            self.assertEqual(s["on_hand"], len(shelf))

    def test_context_mirrors_job_orders(self):
        """Every recording's MorletDB context names the job order the graph has for its unit at its time."""
        jobs = [(p["id"], p["from"], p.get("to"), u["id"]) for gid, (lab, p) in self.g.v.items() if lab == "JobOrder"
                for u, _ in self.g.out("EXECUTED_ON", p["id"])]
        rows = self.conn.execute(
            f"SELECT r.recording_id, r.metadata->>'unit', r.metadata->>'start', x.value #>> '{{}}' "
            f"FROM {plant.COLLECTION} r JOIN {plant.COLLECTION}_ctx x ON x.recording_id = r.recording_id "
            f"AND x.key = 'job_order' AND NOT x.retracted").fetchall()
        self.assertEqual(len(rows), 6256)
        for rid, unit, start, job in rows:
            want = [j for j, a, b, u in jobs if u == unit and a <= start and (b is None or start < b)]
            self.assertEqual(want, [job], rid)


class Test3InstallResolution(unittest.TestCase):
    def test_replacement_mid_history(self):
        with db().connect(fleetdb.READER) as c:
            fleetdb.set_search_path(c)
            old, new = plant.asset_of("2", "1"), plant.asset_of("3", "1")
            r = queries.installed_at(c, "P-102.BRG-1", "2004-02-15T00:00:00Z")
            self.assertEqual(r["installed"]["asset"], old)
            self.assertEqual([h["asset"] for h in r["history"]], [old, new])
            self.assertIsNone(queries.installed_at(c, "P-102.BRG-1", "2004-02-25T00:00:00Z")["installed"])
            self.assertEqual(queries.installed_at(c, "P-102.BRG-1", "2004-03-10T00:00:00Z")["installed"]["asset"], new)
            # half-open: the removal instant belongs to nobody, the install instant to the new bearing
            self.assertIsNone(queries.installed_at(c, "P-102.BRG-1", "2004-02-19T08:00:00Z")["installed"])
            self.assertEqual(queries.installed_at(c, "P-102.BRG-1", "2004-03-04T09:00:00Z")["installed"]["asset"], new)
            self.assertEqual(queries.installed_at(c, "P-102.BRG-1", "2004-03-04T08:59:59Z")["installed"], None)

    def test_every_recording_resolves_to_its_ims_bearing(self):
        """Resolving each recording (position, start time) through INSTALLED_IN gives the bearing that ran as that
        IMS test's bearing: 6,256 resolutions, none ambiguous."""
        with db().connect(fleetdb.READER) as c:
            fleetdb.set_search_path(c)
            installs = fleetdb.cypher(c, "MATCH (a:PhysicalAsset)-[i:INSTALLED_IN]->(m:EquipmentModule) "
                                         "RETURN m.id, a.id, a.ims_test, a.ims_bearing, i.from, i.to", None,
                                      ("m", "a", "t", "b", "f", "e"))
            recs = c.execute(f"SELECT metadata FROM {plant.COLLECTION}").fetchall()
        by_pos = {}
        for i in installs:
            by_pos.setdefault(i["m"], []).append(i)
        n = Counter()
        for (meta,) in recs:
            hits = [i for i in by_pos[meta["equipment_id"]]
                    if i["f"] <= meta["start"] and (i["e"] is None or meta["start"] < i["e"])]
            self.assertEqual(len(hits), 1, meta)
            self.assertEqual((hits[0]["t"], hits[0]["b"]), (meta["ims_test"], meta["ims_bearing"]), meta)
            n[meta["ims_test"]] += 1
        self.assertEqual(dict(n), {"1": 1440, "2": 656, "3": 4160})


class Test4Flagship(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with db().connect(fleetdb.READER, options="-c default_transaction_read_only=on") as c:
            cls.result = investigate.run(c)
        with db().connect(fleetdb.CONTRACTOR, options="-c default_transaction_read_only=on") as c:
            cls.contractor = investigate.run(c)

    def test_answer_from_known_failures(self):
        a = self.result["answer"]
        self.assertEqual(a["changed"]["equipment_id"], "P-102.BRG-3")
        self.assertEqual(a["changed"]["asset"], plant.asset_of("3", "3"))     # IMS test 3, bearing 3
        self.assertGreaterEqual(a["changed"]["ratio"], investigate.CHANGED_RATIO)
        w = a["what_it_was"]
        self.assertTrue(a["seen_before"])
        self.assertEqual(w["asset"], plant.asset_of("2", "1"))                 # IMS test 2, bearing 1
        self.assertEqual(w["failure"], "FL-2004-02-P102-BRG1")
        self.assertEqual(w["mode"], HELD_OUT_TEST3_BEARING3)                    # the readme's outcome of test 3
        self.assertEqual(a["precedents"][0]["first_rank"], 1)
        self.assertGreaterEqual(w["hits"], 40)
        # Bearings of the other test-1 failures (inner race, roller) are not among the 50 closest windows.
        self.assertFalse({p["asset"] for p in a["precedents"]} & {plant.asset_of("1", "3"), plant.asset_of("1", "4")})
        self.assertEqual([r["work_order"]["id"] for r in a["fixed_last_time"]], ["WO-2004-0219-01"])
        self.assertEqual(sorted(x["equipment_id"] for x in a["same_lot"]["installed"]),
                         ["F-113.BRG-1", "F-214.BRG-3", "P-102.BRG-1", "P-102.BRG-2", "P-102.BRG-4", "P-104.BRG-2",
                          "P-203.BRG-4"])
        self.assertEqual({s["store"]: s["from_same_lot"] for s in a["spares"]}, {"AW-N-STORE": 2, "AW-S-STORE": 0})
        self.assertTrue(any("not a diagnosis" in c for c in a["caveats"]))

    def test_trace_records_every_hop(self):
        hops = self.result["trace"]
        self.assertEqual(len(hops), 13)
        for h in hops:
            self.assertTrue(h["query"] and isinstance(h["elapsed_ms"], float) and "result" in h, h["name"])
        joined = [h for h in hops if h["store"].startswith("graph+morletdb")]
        self.assertEqual(len(joined), 1)
        self.assertIn("ag_catalog.cypher", joined[0]["query"])
        self.assertIn("morlet_search_similar", joined[0]["query"])

    def test_matches_committed_reference(self):
        ref = json.loads((DEMO / "reference/flagship_trace.json").read_text())
        strip = [{k: v for k, v in h.items() if k != "elapsed_ms"} for h in self.result["trace"]]
        self.assertEqual(json.loads(json.dumps(strip, default=str)),
                         [{k: v for k, v in h.items() if k != "elapsed_ms"} for h in ref["trace"]])
        self.assertEqual(json.loads(json.dumps(self.result["answer"], default=str)), ref["answer"])

    def test_contractor_sees_one_site(self):
        a = self.contractor["answer"]
        self.assertEqual(a["what_it_was"]["mode"], "FM-OR")     # the precedent is at Site A
        self.assertEqual(sorted(x["equipment_id"] for x in a["same_lot"]["installed"]),
                         ["F-113.BRG-1", "P-102.BRG-1", "P-102.BRG-2", "P-102.BRG-4", "P-104.BRG-2"])
        self.assertEqual([s["store"] for s in a["spares"]], ["AW-N-STORE"])

    def test_mcp_replay(self):
        """The same hops through the MCP servers over stdio (read-only role): same top hit."""
        r = subprocess.run([sys.executable, "-I", str(DEMO / "replay.py"), "--json", "--dsn", db().dsn(fleetdb.READER)],
                           capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = json.loads(r.stdout)
        self.assertFalse([c for c in calls if "error" in c["result"]], calls)
        sim = next(c for c in calls if c["tool"] == "similar_failures")["result"]
        top = self.result["trace"][9]["result"][0]
        self.assertEqual((sim["hits"][0]["recording_id"], sim["hits"][0]["start_sample"]),
                         (top["recording_id"], top["start_sample"]))
        self.assertEqual(sim["hits"][0]["mode"], "FM-OR")

    def test_mcp_connections_are_read_only(self):
        import graph_mcp
        import os
        old = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = db().dsn(fleetdb.ENGINEER)    # even a role that may write
        try:
            with graph_mcp.connect() as c:
                with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                    fleetdb.cypher(c, "MATCH (a:PhysicalAsset {id: 'SN-0402-007'}), (f:Failure) "
                                      "CREATE (a)-[:RESEMBLES {site: 'A'}]->(f)")
        finally:
            if old is None:
                os.environ.pop("DATABASE_URL")
            else:
                os.environ["DATABASE_URL"] = old


class Test5Access(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = checks.check3(db())

    def assert_one_site(self, seen):
        self.assertEqual(seen["labeled MATCH (u:Unit)"], ['"UA"'])
        self.assertEqual(seen["unlabeled MATCH (n)"], ['"E"', '"FM"', '"L"', '"SA"', '"UA"'])
        self.assertEqual(seen["unlabeled edges"], ['"A"', '"A"', '"A"'])
        self.assertEqual(seen["two fixed hops via a shared lot"], [])
        self.assertEqual(seen["three fixed hops to a failure mode"], [])
        for k in checks.BYPASS_READS:
            self.assertIn("permission denied for function age_vle", seen[k])
        self.assertIn("permission denied", seen["forged edge into startNode"])
        self.assertIn("permission denied", seen["vertex_stats"])
        self.assertIn("permission denied for function age_graph_stats", seen["graph_stats"])
        tables = seen["direct SQL per label table"]
        self.assertEqual(tables[[t for t in tables if t.endswith('"Unit"')][0]], 1)
        self.assertEqual(tables[[t for t in tables if t.endswith('"FAILED_AS"')][0]], 0)
        self.assertEqual(seen["MorletDB recordings"], ["ra"])
        for t, n in seen["MorletDB tables (rows)"].items():
            if t.endswith(("_ctx_segments", "_kept_summaries", "_ledger")):
                continue
            self.assertGreater(n, 0, t)
        self.assertEqual(seen["search from ra"], ["ra"])
        self.assertIn("not visible", seen["search from rb"])

    def test_both_orders(self):
        for order in ("rules_first", "data_first"):
            o = self.r[order]
            self.assertEqual(o["gaps_after_securing"], [], order)
            self.assert_one_site(o[fleetdb.CONTRACTOR])
            reader = o[fleetdb.READER]
            self.assertEqual(reader["labeled MATCH (u:Unit)"], ['"UA"', '"UB"'])
            self.assertEqual(reader["MorletDB recordings"], ["ra", "rb"])
            # same companion tables, the contractor sees half of every non-empty one
            for t, n in reader["MorletDB tables (rows)"].items():
                self.assertEqual(o[fleetdb.CONTRACTOR]["MorletDB tables (rows)"][t] * 2, n, t)

    def test_new_label_fails_closed(self):
        o = self.r["rules_first"]
        self.assertTrue(o["gaps_after_data"])
        self.assertIn("permission denied", o["contractor_on_unsecured_new_label"])
        self.assertEqual(o["contractor_unlabeled_match_before_resecuring"], ['"E"', '"FM"', '"L"', '"SA"', '"UA"'])

    def test_age_functions_bypass_rls_when_granted(self):
        """With AGE's default grants, these functions show the contractor what RLS hides (AGE 1.7.0: the global graph
        behind variable-length paths and the stats functions is loaded without RLS; apache/age#2561 fixes it on
        master). If this ever fails, AGE changed: update CHECKS.md and security.sql."""
        b = self.r["data_first"]["contractor with the AGE functions granted (AGE's default)"]
        self.assertEqual(b["variable-length path lot -[*2]- failure mode"], ['"FM"'])
        self.assertIn('"site": "B"', b["variable-length path, its edges"][0])
        self.assertIn('"in_degree": 1, "out_degree": 2', b["vertex_stats of the hidden unit"][0])
        # 7 vertices and 7 edges in all; the contractor may see 5 and 3
        self.assertIn('"num_loaded_edges": 7, "num_loaded_vertices": 7', b["graph_stats"][0])

    def test_age_checks_rls_in_startnode_endnode(self):
        """AGE 1.7.0 (apache/age#2309) checks RLS when startNode/endNode look a vertex up by id; with AGE 1.6.0 the
        forged edge returned the hidden unit. The functions stay restricted anyway (defence in depth)."""
        b = self.r["data_first"]["contractor with the AGE functions granted (AGE's default)"]
        for k in ("forged edge into startNode", "forged edge into endNode"):
            self.assertIn("denied by row-level security policy", b[k])
            self.assertNotIn('"UB"', b[k])

    def test_no_event_trigger_for_new_labels(self):
        e = self.r["event trigger on new labels"]
        self.assertIn("ByFunction", e["labels created"])
        self.assertIn("ByCreate", e["labels created"])
        self.assertEqual(e["ddl events seen"], [["CREATE TABLE", "fleet.ddl_control"]])

    def test_regrant_detected(self):
        g = self.r["regrant"]
        self.assertTrue(any(fleetdb.CONTRACTOR in row[0] for row in g["gaps_while_granted"]))
        self.assertEqual(g["gaps_after_revoking"], [])

    def test_demo_contractor(self):
        c, r = self.r["demo"][fleetdb.CONTRACTOR], self.r["demo"][fleetdb.READER]
        self.assertEqual(c["graph sites"], ['*', 'A'])
        self.assertEqual(c["units at site B"], [])
        self.assertEqual(c["recordings by site"], {"A": 4816})
        self.assertEqual(r["recordings by site"], {"A": 4816, "B": 1440})
        for t in ("fleet.vibration", "fleet.vibration_chunks", "fleet.vibration_envelopes", "fleet.vibration_ctx",
                  "fleet.vibration_segments"):
            self.assertEqual(c["distinct recordings per table"][t], 4816, t)
        self.assertEqual(c["F-212 history"], "not visible")


class Test6WriteBack(unittest.TestCase):
    def test_atomic(self):
        r = checks.check4(db())
        for case in ("injected failure before commit", "MorletDB refuses the episode (interval beyond the recording)"):
            self.assertTrue(r[case]["status"].startswith("rolled back"), r[case])
            self.assertEqual(r[case]["after"], {"graph_edges": 0, "labels": 0, "episodes": 0}, case)
        self.assertEqual(r["committed"], {"status": "written",
                                          "after": {"graph_edges": 1, "labels": 1, "episodes": 1}})
        self.assertEqual(r["identical retry"], "exists")
        self.assertTrue(r["conflicting retry"].startswith("refused"))
        self.assertEqual(r["engineer changes the recorded finding (SET)"], "permission denied for table RESEMBLES")
        self.assertTrue(r["contractor writes"].startswith("refused: permission denied"))

    def test_cli_needs_the_typed_id(self):
        ref = DEMO / "reference/flagship_trace.json"
        finding = confirm_finding.finding_from(json.loads(ref.read_text())["answer"])
        args = [sys.executable, "-I", str(DEMO / "confirm_finding.py"), "--trace", str(ref), "--confirmed-by",
                "PC-RELENG", "--dsn", db().dsn(fleetdb.ENGINEER)]
        r = subprocess.run(args, input="yes\n", capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        with db().connect() as c:
            self.assertEqual(checks._counts(c, finding["finding_id"]), {"graph_edges": 0, "labels": 0, "episodes": 0})
        r = subprocess.run(args, input=finding["finding_id"] + "\n", capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with db().connect() as c:
            self.assertEqual(checks._counts(c, finding["finding_id"]), {"graph_edges": 1, "labels": 1, "episodes": 1})
            label = c.execute(f"SELECT recording_id, start_sample, end_sample, label FROM {plant.COLLECTION}_labels "
                              f"WHERE note LIKE %s", (f'%{finding["finding_id"]}%',)).fetchone()
            self.assertEqual(label, (finding["query"]["recording_id"], 0, 4096, "finding:resembles"))
        checks.remove_finding(db(), finding["finding_id"])


class Test7Snapshot(unittest.TestCase):
    def test_one_snapshot(self):
        r = checks.check5(db())
        rr = r["REPEATABLE READ"]
        self.assertTrue(rr["writer committed"])
        self.assertEqual(rr["after the concurrent commit"], rr["before"])
        self.assertEqual({k: rr["new transaction"][k] for k in ("graph_edges", "labels", "episodes")},
                         {"graph_edges": 1, "labels": 1, "episodes": 1})
        rc = r["READ COMMITTED"]["after the concurrent commit"]
        self.assertEqual((rc["graph_edges"], rc["labels"], rc["episodes"]), (1, 1, 1))
        n = r["new recording and its graph vertex"]
        self.assertEqual(n["after the concurrent commit"], n["before"])
        self.assertEqual(n["new transaction"]["recordings"], n["before"]["recordings"] + 1)
        self.assertEqual(n["new transaction"]["unit vertices"], n["before"]["unit vertices"] + 1)
        self.assertIn("rc", n["new transaction"]["search hits from ra"])


if __name__ == "__main__":
    unittest.main()
