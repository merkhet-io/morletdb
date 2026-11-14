"""The MCP tool labels against a real database: counts, glossary, undefined and retracted labels, decision labels,
and row-level security for a non-owner role in both enablement orders.

Needs MORLET_MCP_TEST_DATABASE_URL (a database where the role may create the extension, tables and roles)."""
import asyncio
import json
import os
import unittest

import psycopg
from psycopg.conninfo import make_conninfo

import morlet_mcp as m

URL = os.environ.get("MORLET_MCP_TEST_DATABASE_URL")
READER = "ltest_reader"


def call(collection, url=URL, **kw):
    os.environ["DATABASE_URL"] = url
    r = asyncio.run(m.mcp.call_tool("labels", {"collection": collection, **kw}))
    return json.loads(r.content[0].text)


def make_collection(c, name, rls_first):
    for t in ("label_definitions", "labels", "ctx_values", "ctx_segments", "ctx", "chunks", "segments"):
        c.execute(f"DROP TABLE IF EXISTS public.{name}_{t} CASCADE")
    c.execute(f"DROP TABLE IF EXISTS public.{name} CASCADE")
    c.execute(f"SELECT morlet_create_stream_collection('public','{name}')")
    c.execute(f"ALTER TABLE public.{name} ENABLE ROW LEVEL SECURITY")
    c.execute(f"CREATE POLICY site ON public.{name} USING (metadata->>'site' = 'A')")
    if rls_first:                                     # row security first, context (and its label tables) after
        c.execute(f"SELECT morlet_enable_row_security('public.{name}'::regclass)")
    c.execute(f"SELECT morlet_enable_context('public.{name}')")
    if not rls_first:
        c.execute(f"SELECT morlet_enable_row_security('public.{name}'::regclass)")
    for rid, site in (("a1", "A"), ("a2", "A"), ("b1", "B")):
        c.execute(f"SELECT morlet_start_recording('public.{name}',%s,1000,'x','V',%s::jsonb,512,256,'zscore','none',1)",
                  (rid, json.dumps({"site": site})))
        c.execute(f"SELECT morlet_append_chunk('public.{name}',%s,0,%s::float8[])", (rid, [0.0] * 2048))
    add = f"SELECT morlet_add_label('public.{name}',%s,%s,%s,%s,%s)"
    c.execute(add, ("a1", 0, 1000, "worn", "flank wear 0.25 mm"))
    c.execute(add, ("a2", 100, 300, "worn", ""))
    c.execute(add, ("a2", 0, 50, "chatter", "heard at the machine"))
    c.execute(add, ("b1", 0, 2048, "worn", "site B"))
    c.execute(add, ("b1", 0, 2048, "secret_b", ""))
    c.execute(add, ("a1", 1000, 2048, "s1:replace", "{}"))
    lid = c.execute(add, ("a1", 0, 10, "typo", "")).fetchone()[0]
    c.execute(f"SELECT morlet_retract_label('public.{name}',%s)", (lid,))
    c.execute(f"SELECT morlet_define_label('public.{name}','worn','flank wear above 0.2 mm','mm','ISO 8688-1')")
    c.execute(f"SELECT morlet_define_label('public.{name}','sharp','new or reground tool','','shop rule')")
    c.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READER}")


@unittest.skipUnless(URL, "needs MORLET_MCP_TEST_DATABASE_URL")
class LabelsOnADatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg.connect(URL, autocommit=True)
        c = cls.conn
        c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READER,)).fetchone():
            c.execute(f"CREATE ROLE {READER} LOGIN")
        make_collection(c, "ltest", rls_first=False)
        make_collection(c, "ltest2", rls_first=True)
        c.execute("DROP TABLE IF EXISTS public.lnoctx CASCADE; DROP TABLE IF EXISTS public.lnoctx_chunks CASCADE; "
                  "DROP TABLE IF EXISTS public.lnoctx_segments CASCADE")
        c.execute("SELECT morlet_create_stream_collection('public','lnoctx')")
        cls.reader_url = make_conninfo(URL, user=READER)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_counts_glossary_and_undefined(self):
        out = call("public.ltest")
        by = {r["label"]: r for r in out["labels"]}
        self.assertEqual(set(by), {"worn", "chatter", "secret_b", "typo"})   # s1: decisions left out
        w = by["worn"]
        self.assertEqual((w["intervals"], w["recordings"], w["samples"]), (3, 3, 1000 + 200 + 2048))
        self.assertEqual(w["definition"], {"definition": "flank wear above 0.2 mm", "unit": "mm",
                                           "source": "ISO 8688-1"})
        self.assertEqual(by["chatter"]["definition"], None)
        self.assertEqual(out["undefined_labels"], ["chatter", "secret_b"])  # retracted "typo" is not active
        self.assertEqual((by["typo"]["intervals"], by["typo"]["retracted"], by["typo"]["examples"]), (0, 1, []))
        self.assertEqual(out["unused_definitions"], ["sharp"])
        self.assertTrue(out["glossary_available"])
        self.assertEqual(w["examples"][0], {"recording_id": "a1", "start_sample": 0, "end_sample": 1000,
                                            "note": "flank wear 0.25 mm", "author": w["examples"][0]["author"]})

    def test_decisions_prefix_and_example_limit(self):
        out = call("public.ltest", include_decisions=True, examples=1)
        by = {r["label"]: r for r in out["labels"]}
        self.assertEqual(by["s1:replace"]["intervals"], 1)
        self.assertEqual(len(by["worn"]["examples"]), 1)
        a2 = {r["label"]: r for r in call("public.ltest", recording_prefix="a2")["labels"]}
        self.assertEqual(set(a2), {"worn", "chatter"})
        self.assertEqual(a2["worn"]["samples"], 200)
        self.assertEqual(call("public.ltest", recording_prefix="a_")["labels"], [])   # _ is literal, not a wildcard

    def test_a_collection_without_context_is_an_error(self):
        with self.assertRaises(Exception) as e:
            call("public.lnoctx")
        self.assertIn("morlet_enable_context", str(e.exception))

    def test_row_security_holds_for_a_reader_in_both_orders(self):
        for coll in ("public.ltest", "public.ltest2"):
            with self.subTest(collection=coll):
                out = call(coll, url=self.reader_url)
                by = {r["label"]: r for r in out["labels"]}
                self.assertNotIn("secret_b", by)                      # only on a site-B recording
                self.assertEqual((by["worn"]["intervals"], by["worn"]["recordings"]), (2, 2))
                self.assertNotIn("b1", [e["recording_id"] for r in out["labels"] for e in r["examples"]])
                owner = {r["label"]: r for r in call(coll)["labels"]}
                self.assertEqual(owner["worn"]["intervals"], 3)


if __name__ == "__main__":
    unittest.main()
