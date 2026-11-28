"""influx_source: reading InfluxDB 1.x (JSON) and 2.x (annotated CSV) over HTTP, exactly. A local fake server stands
in for InfluxDB; the live test against real InfluxDB 1.8 and 2.7 is test_influx_source_live.py."""
import http.server
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
import urllib.parse
from unittest import mock

import influx_lp as lp
import influx_source as src
import morlet_import as mi

T0 = 1767225600000000000


class Fake(http.server.BaseHTTPRequestHandler):
    version = "1.8.10"
    seen = []

    def log_message(self, *a):
        pass

    def reply(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("X-Influxdb-Version", self.version)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        Fake.seen.append((self.path, self.headers.get("Authorization")))
        u = urllib.parse.urlsplit(self.path)
        if u.path == "/ping":
            return self.reply(204, "")
        params = urllib.parse.parse_qs(u.query)
        q = params["q"][0]
        if params["db"][0] == "nosuch":
            return self.reply(200, '{"results":[{"statement_id":0,"error":"database not found: nosuch"}]}')
        if q.startswith("SHOW FIELD KEYS"):
            return self.reply(200, json.dumps({"results": [{"statement_id": 0, "series": [{"name": "vib", "columns":
                ["fieldKey", "fieldType"], "values": [["a", "float"], ["n", "integer"], ["s", "string"], ["ok", "boolean"]]}]}]}))
        a = int(q.split("time >= ")[1].split(" ")[0])
        b = int(q.split("time < ")[1].split(" ")[0])
        rows = [[t, v, n, s, ok] for t, v, n, s, ok in V1_ROWS if a <= t < b]
        body = ('{"results":[{"statement_id":0,"series":[{"name":"vib","tags":{"machine":"line 3","axis":"z,1"},'
                '"columns":["time","a","n","s","ok"],"values":[' + ",".join(
                    "[" + ",".join(x for x in r_text) + "]" for r_text in ([str(t), v, n, s, ok] for t, v, n, s, ok in rows))
                + "]}]}]}")
        return self.reply(200, body)

    def do_POST(self):
        Fake.seen.append((self.path, self.headers.get("Authorization")))
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "nosuch" in body["query"]:
            return self.reply(400, '{"code":"invalid","message":"bucket not found"}')
        return self.reply(200, V2_CSV, "text/csv")


# 1.x JSON as InfluxDB writes it: -0 for a negative zero, "2" for an integer-valued float, a 2^60 integer
V1_ROWS = [(T0, "-0", "1", '"x"', "true"), (T0 + 1_000_000, "2", "1152921504606846976", "null", "false"),
           (T0 + 2_000_000, "0.30000000000000004", "-3", '"y, z"', "null"), (T0 + 3_000_000, "1e-300", "null", "null", "true")]

V2_CSV = """#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,double,string,string,string,string
,result,table,_start,_stop,_time,_value,_field,_measurement,axis,machine
,,0,2026-01-01T00:00:00Z,2026-01-01T00:00:01Z,2026-01-01T00:00:00Z,-0,a,vib,"z,1",line 3
,,0,2026-01-01T00:00:00Z,2026-01-01T00:00:01Z,2026-01-01T00:00:00.001Z,2,a,vib,"z,1",line 3

#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,unsignedLong,string,string,string,string
,result,table,_start,_stop,_time,_value,_field,_measurement,axis,machine
,,1,2026-01-01T00:00:00Z,2026-01-01T00:00:01Z,2026-01-01T00:00:00.000000001Z,18446744073709551615,u,vib,"z,1",line 3

"""


class Source(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Fake.seen = []
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.out = Path(self.dir.name) / "source.lp"

    def series(self):
        with open(self.out) as f:
            return lp.read_series(f)

    def test_v1_values_keep_their_type_sign_and_digits(self):
        Fake.version = "1.8.10"
        with mock.patch.dict(os.environ, {"INFLUX_USERNAME": "reader", "INFLUX_PASSWORD": "pw"}):
            prov = src.fetch(self.url, self.out, "vib", T0, T0 + 4_000_000, db="plant", slice_s=0.002, pause_s=0)
        self.assertEqual((prov["slices"], prov["lines"], prov["influxdb_version"]), (2, 4, "1.8.10"))
        self.assertNotIn("pw", json.dumps(prov))
        s = self.series()
        a = s[("vib", (("axis", "z,1"), ("machine", "line 3")), "a")]
        self.assertEqual(a.kind, "float")
        self.assertEqual([struct.pack("<d", v) for v in a.values],
                         [struct.pack("<d", x) for x in (-0.0, 2.0, 0.30000000000000004, 1e-300)])
        n = s[("vib", (("axis", "z,1"), ("machine", "line 3")), "n")]
        self.assertEqual((n.kind, n.values), ("integer", [1, 2 ** 60, -3]))
        self.assertEqual(s[("vib", (("axis", "z,1"), ("machine", "line 3")), "s")].values, ["x", "y, z"])
        self.assertTrue(all(auth and auth.startswith("Basic ") for path, auth in Fake.seen if "/query" in path))
        self.assertTrue(all("pw" not in path for path, _ in Fake.seen))            # never in the URL

    def test_v2_values_from_annotated_csv(self):
        Fake.version = "v2.7.12"
        with mock.patch.dict(os.environ, {"INFLUX_TOKEN": "tok"}):
            prov = src.fetch(self.url, self.out, "vib", T0, T0 + 1_000_000_000, org="plant", bucket="machines",
                             pause_s=0, iso_ns=mi.iso_ns)
        self.assertEqual((prov["influxdb_version"], prov["lines"]), ("2.7.12", 3))
        s = self.series()
        a = s[("vib", (("axis", "z,1"), ("machine", "line 3")), "a")]
        self.assertEqual((a.kind, a.times, [struct.pack("<d", v) for v in a.values]),
                         ("float", [T0, T0 + 1_000_000], [struct.pack("<d", -0.0), struct.pack("<d", 2.0)]))
        u = s[("vib", (("axis", "z,1"), ("machine", "line 3")), "u")]
        self.assertEqual((u.kind, u.values, u.times), ("unsigned", [2 ** 64 - 1], [T0 + 1]))
        self.assertTrue(all(auth == "Token tok" for path, auth in Fake.seen if "/query" in path))

    def test_refusals_are_clear(self):
        Fake.version = "v2.7.12"
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(src.SourceError, "INFLUX_TOKEN"):
            src.fetch(self.url, self.out, "vib", T0, T0 + 10, org="o", bucket="b", pause_s=0, iso_ns=mi.iso_ns)
        with mock.patch.dict(os.environ, {"INFLUX_TOKEN": "t"}), self.assertRaisesRegex(src.SourceError, "400.*bucket"):
            src.fetch(self.url, self.out, "vib", T0, T0 + 10, org="o", bucket="nosuch", pause_s=0, iso_ns=mi.iso_ns)
        with self.assertRaisesRegex(src.SourceError, "--influx-org"):
            src.fetch(self.url, self.out, "vib", T0, T0 + 10, db="plant")
        Fake.version = "1.8.10"
        with self.assertRaisesRegex(src.SourceError, "database not found"):
            src.fetch(self.url, self.out, "vib", T0, T0 + 10, db="nosuch", pause_s=0)
        with self.assertRaisesRegex(src.SourceError, "after --start"):
            src.fetch(self.url, self.out, "vib", T0, T0, db="plant")
        Fake.version = "3.1.0"
        with self.assertRaisesRegex(src.SourceError, "not supported"):
            src.fetch(self.url, self.out, "vib", T0, T0 + 10, db="plant")
        with self.assertRaisesRegex(src.SourceError, "cannot reach"):
            src.fetch("http://127.0.0.1:9", self.out, "vib", T0, T0 + 10, db="plant")

    def test_the_point_limit_stops_reading(self):
        Fake.version = "1.8.10"
        with self.assertRaisesRegex(src.SourceError, "more than 3 points"):
            src.fetch(self.url, self.out, "vib", T0, T0 + 4_000_000, db="plant", slice_s=0.004, pause_s=0, max_points=3)

    def test_written_lines_parse_back_identically(self):
        line = src.lp_line("m x", {"t,1": "a b", "empty": ""}, [("f=1", "float", -0.0), ("s", "string", 'q"\\')], 5)
        (row1, row2) = lp.parse_line(line)
        self.assertEqual((row1[0], row1[1], row1[2], struct.pack("<d", row1[4])), ("m x", (("t,1", "a b"),), "f=1",
                                                                                   struct.pack("<d", -0.0)))
        self.assertEqual(row2[4], 'q"\\')


if __name__ == "__main__":
    unittest.main()
