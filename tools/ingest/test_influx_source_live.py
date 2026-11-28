"""influx_source against real InfluxDB 1.x and 2.x: write synthetic points with an admin, read them back read-only,
compare bit for bit. Runs only when the servers are given (it writes to them; use disposable containers):

  docker run -d -p 127.0.0.1:18086:8086 -e INFLUXDB_DB=plant -e INFLUXDB_HTTP_AUTH_ENABLED=true \\
    -e INFLUXDB_ADMIN_USER=admin -e INFLUXDB_ADMIN_PASSWORD=adminpw -e INFLUXDB_READ_USER=reader \\
    -e INFLUXDB_READ_USER_PASSWORD=readpw influxdb:1.8
  docker run -d -p 127.0.0.1:18087:8086 -e DOCKER_INFLUXDB_INIT_MODE=setup -e DOCKER_INFLUXDB_INIT_USERNAME=admin \\
    -e DOCKER_INFLUXDB_INIT_PASSWORD=adminpw -e DOCKER_INFLUXDB_INIT_ORG=plant -e DOCKER_INFLUXDB_INIT_BUCKET=machines \\
    -e DOCKER_INFLUXDB_INIT_ADMIN_TOKEN=admintoken influxdb:2.7
  MORLET_TEST_INFLUX1=http://admin:adminpw@127.0.0.1:18086/plant?reader=reader:readpw \\
  MORLET_TEST_INFLUX2=http://127.0.0.1:18087/plant/machines?token=admintoken \\
    python -m unittest test_influx_source_live
"""
import base64
import math
import os
from pathlib import Path
import random
import secrets
import struct
import tempfile
import unittest
import urllib.parse
import urllib.request
from unittest import mock

import influx_lp as lp
import influx_source as src
import morlet_import as mi

T0 = 1767225600000000000
V1, V2 = os.environ.get("MORLET_TEST_INFLUX1"), os.environ.get("MORLET_TEST_INFLUX2")


def points(measurement):
    rnd = random.Random(5)
    lines = []
    for n in range(4000):                               # 4 kHz, 1 s, jitter, a negative zero, full-precision floats
        t = T0 + n * 250_000 + rnd.randint(-15_000, 15_000)
        v = -0.0 if n == 0 else 0.3 * math.sin(n / 7) + rnd.gauss(0, 0.01)
        lines.append(src.lp_line(measurement, {"machine": "line 3", "axis": "z,1"},
                                 [("a", "float", v), ("n", "integer", int(v * 32768) + 2 ** 60)], t))
    return "\n".join(lines) + "\n"


def write(url, headers, body):
    req = urllib.request.Request(url, data=body.encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        assert r.status == 204, r.status


class Live(unittest.TestCase):
    def check(self, out, text):
        with open(out) as f:
            got = lp.read_series(f)
        want = lp.read_series(text.splitlines())
        self.assertEqual(set(got), set(want))
        for k, s in want.items():
            g = got[k]
            self.assertEqual(g.kind, s.kind)
            self.assertEqual(sorted(g.times), sorted(s.times))
            pack = (lambda v: struct.pack("<d", v)) if s.kind == "float" else (lambda v: v)
            self.assertEqual([pack(v) for _, v in sorted(zip(g.times, g.values))],
                             [pack(v) for _, v in sorted(zip(s.times, s.values))])

    @unittest.skipUnless(V1, "needs MORLET_TEST_INFLUX1")
    def test_influxdb_1(self):
        u = urllib.parse.urlsplit(V1)
        base, db = f"{u.scheme}://{u.hostname}:{u.port}", u.path.strip("/")
        reader, rpw = urllib.parse.parse_qs(u.query)["reader"][0].split(":", 1)
        m = "vib_" + secrets.token_hex(3)
        text = points(m)
        auth = base64.b64encode(f"{u.username}:{u.password}".encode()).decode()
        write(f"{base}/write?db={db}&precision=ns", {"Authorization": f"Basic {auth}"}, text)
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"INFLUX_USERNAME": reader,
                                                                               "INFLUX_PASSWORD": rpw}):
            out = Path(d) / "source.lp"
            prov = src.fetch(base, out, m, T0 - 10 ** 9, T0 + 2 * 10 ** 9, db=db, slice_s=0.25, pause_s=0.05)
            self.assertTrue(prov["influxdb_version"].startswith("1."))
            self.check(out, text)

    @unittest.skipUnless(V2, "needs MORLET_TEST_INFLUX2")
    def test_influxdb_2(self):
        u = urllib.parse.urlsplit(V2)
        base = f"{u.scheme}://{u.hostname}:{u.port}"
        org, bucket = u.path.strip("/").split("/")
        token = urllib.parse.parse_qs(u.query)["token"][0]
        m = "vib_" + secrets.token_hex(3)
        text = points(m)
        write(f"{base}/api/v2/write?org={org}&bucket={bucket}&precision=ns", {"Authorization": f"Token {token}"}, text)
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"INFLUX_TOKEN": token}):
            out = Path(d) / "source.lp"
            prov = src.fetch(base, out, m, T0 - 10 ** 9, T0 + 2 * 10 ** 9, org=org, bucket=bucket, slice_s=0.25,
                             pause_s=0.05, iso_ns=mi.iso_ns)
            self.assertTrue(prov["influxdb_version"].startswith("2."))
            self.check(out, text)


if __name__ == "__main__":
    unittest.main()
