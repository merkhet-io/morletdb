"""InfluxDB line protocol: the parser, and the importer's influx reader. No database."""
import argparse
import contextlib
from fractions import Fraction
import io
import json
import struct
import tempfile
from pathlib import Path
import unittest

import influx_lp as lp
import morlet_import as mi

T0 = 1767225600000000000


def opts(**kw):
    o = dict(format=None, unit=None, prefix="", precision="ns", min_rate=100.0, max_points=None)
    o.update(kw)
    return argparse.Namespace(**o)


class Parser(unittest.TestCase):
    def test_escapes_types_and_order(self):
        rows = lp.parse_line(r'my\ meas,z=1,host=a\,b,t=x\=y f\ 1=1.5,f2=-3i,f3=7u,s="a, \"q\" b",b=t 1700000000000000000')
        self.assertEqual([(r[0], r[1], r[2], r[3], r[4]) for r in rows], [
            ("my meas", (("host", "a,b"), ("t", "x=y"), ("z", "1")), "f 1", "float", 1.5),
            ("my meas", (("host", "a,b"), ("t", "x=y"), ("z", "1")), "f2", "integer", -3),
            ("my meas", (("host", "a,b"), ("t", "x=y"), ("z", "1")), "f3", "unsigned", 7),
            ("my meas", (("host", "a,b"), ("t", "x=y"), ("z", "1")), "s", "string", 'a, "q" b'),
            ("my meas", (("host", "a,b"), ("t", "x=y"), ("z", "1")), "b", "boolean", True)])

    def test_floats_are_the_correctly_rounded_float64_and_keep_their_text(self):
        for text in ("0.1", "-0.0", "1e-300", "4.9e-324", "1.7976931348623157e308", "123456789.123456789"):
            (_, _, _, kind, v, t, _), = lp.parse_line(f"m v={text} 1")
            self.assertEqual((kind, t), ("float", text))
            exact = Fraction(text)                       # independent oracle: the nearest float64 to the exact value
            oracle = -0.0 if text.startswith("-") and exact == 0 else float(exact)
            self.assertEqual(struct.pack("<d", v), struct.pack("<d", oracle))

    def test_precision_and_refusals(self):
        self.assertEqual(lp.parse_line("m v=1 5", "ms")[0][6], 5_000_000)
        self.assertEqual(lp.parse_line("# comment"), [])
        self.assertEqual(lp.parse_line("   "), [])
        for bad, why in [("m v=1", "missing timestamp"), ("m v=nan 1", "no 'nan'"), ("m v=1u2 1", "bad field value"),
                         ("m 1", "bad field"), ("m,t v=1 1", "bad tag"), ('m v="x 1', "unterminated"),
                         ("m v=-3u 1", "bad unsigned"), ("m v=1 1.5", "bad timestamp"), (",t=1 v=1 1", "measurement"),
                         ("m v=--3i 1", "bad integer"), ("m v=3-i 1", "bad integer"), ('m v="a"b"c" 1', "must be escaped")]:
            with self.subTest(bad=bad), self.assertRaisesRegex(lp.LineProtocolError, why):
                lp.parse_line(bad)

    def test_escaped_backslashes_and_decimal_places(self):
        rows = lp.parse_line(r"m a\\=1,b\=c=2 1")                # key "a\" then key "b=c"
        self.assertEqual([(r[2], r[4]) for r in rows], [("a\\", 1.0), ("b=c", 2.0)])
        self.assertEqual([lp.decimal_places(t) for t in ("1.5e-05", "3", "-0.250", "1E+3")], [6, 0, 3, 0])

    def test_type_changes_and_the_point_limit_are_refused(self):
        with self.assertRaisesRegex(lp.LineProtocolError, "line 2: .*changes type"):
            lp.read_series(["m v=1 1\n", "m v=1i 2\n"])
        with self.assertRaisesRegex(lp.LineProtocolError, "more than 2 points"):
            lp.read_series(["m v=1 1\n", "m v=1 2\n", "m v=1 3\n"], max_points=2)
        with self.assertRaisesRegex(lp.LineProtocolError, "precision"):
            lp.read_series([], "minutes")
        with self.assertRaisesRegex(lp.LineProtocolError, "positive"):
            lp.read_series(["m v=1 1\n"], max_points=0)


class Reader(unittest.TestCase):
    def read(self, text, **kw):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "export.lp"
            p.write_text(text)
            recs = mi.recordings(p, opts(**kw))
            return recs, mi.SKIPPED[str(p)]

    def test_series_become_recordings_and_trends_are_left_out(self):
        lines = [f"vib,axis=x a={0.001 * (n % 7):.3f},flag=t {T0 + n * 1_000_000}" for n in range(2000)]
        lines += [f"env temp=2{s}.5 {T0 + s * 1_000_000_000}" for s in range(5)]
        recs, skipped = self.read("\n".join(lines) + "\n")
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertTrue(r.recording_id.startswith("export.vib[axis=x].a~"))
        self.assertEqual((r.rate, r.channel, r.quantum, r.samples.size), (1000.0, "a", 0.001, 2000))
        self.assertEqual((r.metadata["measurement"], r.metadata["tags"], r.metadata["start_ns"]), ("vib", {"axis": "x"}, T0))
        self.assertEqual(r.anchors, [(0, T0, 1, False)])               # even timestamps: one anchor
        self.assertEqual(r.times_ns[1], T0 + 1_000_000)
        self.assertEqual(sorted(s["series"] for s in skipped), ["env.temp", "vib[axis=x].flag"])
        self.assertIn("trend", next(s["reason"] for s in skipped if s["series"] == "env.temp"))

    def test_out_of_order_points_duplicates_and_gaps(self):
        n = list(range(1000)) + list(range(1200, 2000))          # a 200-sample gap
        lines = [f"m v={k}i {T0 + k * 1_000_000}" for k in reversed(n)] + [f"m v=5i {T0 + 5_000_000}"]  # dup, same value
        recs, _ = self.read("\n".join(lines) + "\n")
        r = recs[0]
        self.assertEqual(r.samples.tolist(), [float(k) for k in n])
        self.assertEqual([(a[0], a[1], a[3]) for a in r.anchors], [(0, T0, False), (1000, T0 + 1_200_000_000, True)])

    def test_rate_from_jittered_timestamps(self):
        jitter = [0, 7, -5, 3, -8, 6, -2, 4, -6, 1] * 300
        lines = [f"m v=1 {T0 + k * 250_000 + 1000 * jitter[k]}" for k in range(3000)]   # 4 kHz, +-8 us jitter
        r = self.read("\n".join(lines) + "\n")[0][0]
        self.assertAlmostEqual(r.rate, 4000.0, delta=0.05)

    def test_conflicts_and_values_without_an_exact_float64_are_refused(self):
        text = "\n".join([f"a v={k} {T0 + k * 1_000_000}" for k in range(300)] + [f"a v=99 {T0}"]
                         + [f"b v={2 ** 53 + 1}i {T0 + k * 1_000_000}" for k in range(300)]
                         + [f"c v=2e9 {T0 + k * 1_000_000}" for k in range(300)] + [f"d v=1 {T0}"]) + "\n"
        recs, skipped = self.read(text)
        self.assertEqual(recs, [])
        self.assertEqual({s["series"]: s["reason"].split()[0] for s in skipped},
                         {"a.v": "two", "b.v": "an", "c.v": "values", "d.v": "fewer"})

    def test_ambiguous_names_still_give_distinct_recordings(self):
        text = "\n".join([f"a.b c=1 {T0 + k * 1_000_000}" for k in range(200)]
                         + [f"a b.c=2 {T0 + k * 1_000_000}" for k in range(200)]) + "\n"
        recs, _ = self.read(text)
        self.assertEqual(len({r.recording_id for r in recs}), 2)           # both read as "a.b.c"

    def test_signed_zero_duplicates_long_spans_and_min_rate_zero(self):
        text = "\n".join([f"z v={k}.0 {T0 + k * 1_000_000}" for k in range(1, 200)] + [f"z v=0.0 {T0}", f"z v=-0.0 {T0}"]
                         + [f"s v=1 {T0}", f"s v=2 {T0 + 2 ** 53}"]
                         + [f"slow v={k} {T0 + k * 10 ** 9}" for k in range(5)]) + "\n"
        recs, skipped = self.read(text, min_rate=0.0)
        reasons = {x["series"]: x["reason"] for x in skipped}
        self.assertIn("two values", reasons["z.v"])                       # 0.0 and -0.0 differ bit for bit
        self.assertIn("104 days", reasons["s.v"])
        self.assertEqual([r.recording_id.split("~")[0] for r in recs], ["export.slow.v"])   # 1 Hz kept at --min-rate 0
        with self.assertRaisesRegex(ValueError, "min-rate"):
            self.read(text, min_rate=float("nan"))

    def test_nothing_importable_stops_before_the_database(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "trends.lp"
            p.write_text("\n".join(f"env temp=2{k}.5 {T0 + k * 10 ** 9}" for k in range(5)) + "\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = mi.main([str(p), "--collection", "public.x", "--dsn", "host=/nonexistent"])
            self.assertEqual(code, 1)
            out = json.loads(err.getvalue())
            self.assertEqual((out["status"], out["skipped"][0]["series"]), ("nothing to import", "env.temp"))

    def test_same_series_in_same_named_files_is_refused_before_the_database(self):
        text = "\n".join(f"m v=1 {T0 + k * 1_000_000}" for k in range(200)) + "\n"
        with tempfile.TemporaryDirectory() as d:
            paths = [Path(d) / x / "export.lp" for x in ("a", "b")]
            for p in paths:
                p.parent.mkdir()
                p.write_text(text)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = mi.main([*map(str, paths), "--collection", "public.x", "--dsn", "host=/nonexistent"])
            self.assertEqual(code, 1)
            self.assertIn("comes from both", err.getvalue())

    def test_reading_twice_does_not_repeat_the_skip_list(self):
        text = "\n".join(f"m v=1,s=\"x\" {T0 + k * 1_000_000}" for k in range(200)) + "\n"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "export.lp"
            p.write_text(text)
            mi.recordings(p, opts())
            mi.recordings(p, opts())
            self.assertEqual(len(mi.SKIPPED[str(p)]), 1)


if __name__ == "__main__":
    unittest.main()
