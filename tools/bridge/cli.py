#!/usr/bin/env python3
"""Command line for the MorletDB metric bridge (#639). The database comes from the usual libpq environment
(PGHOST, PGDATABASE, ...) or --dsn.

  cli.py install
  cli.py register COLLECTION RECORDING ASSET CHANNEL [--window-samples N]
  cli.py drain SOURCE_ID [--max-windows N]          publish complete windows (one transaction each)
  cli.py add-sink NAME postgres [TARGET_TABLE]      default target: morlet_bridge.metrics
  cli.py add-sink NAME line [--measurement M] [--tag KEY=VALUE ...]
  cli.py deliver NAME [--limit N]                   PostgreSQL sink, exactly once
  cli.py lines NAME [--limit N]                     line protocol to stdout once, at least once
  cli.py telegraf NAME [--drain-all] [--signal stdin|none] [--interval S]
                                                    long-running Telegraf execd input
  cli.py status [SOURCE_ID | --sink NAME]
"""
import argparse
import json
from pathlib import Path
import sys
import time

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from morlet_bridge import Bridge  # noqa: E402


def connect(dsn):
    return psycopg.connect(dsn or "", autocommit=True,
                           options="-c statement_timeout=60000 -c lock_timeout=10000")


def drain_all(b, max_windows):
    total = 0
    for s in b.all("SELECT source_id FROM morlet_bridge.sources ORDER BY source_id"):
        try:
            total += b.drain(s["source_id"], max_windows)["published_windows"]
        except (ValueError, psycopg.Error) as exc:  # a failing source stops only itself; Telegraf logs stderr
            print(f"morlet-bridge: source {s['source_id']}: {exc}", file=sys.stderr, flush=True)
    return total


def telegraf(b, name, drain, signal, interval, max_windows):
    """Telegraf `execd` input. With signal "stdin", Telegraf writes a newline at each collection interval and the
    bridge answers with the pending points; with "none", the bridge writes on its own every `interval` seconds.
    Telegraf does not acknowledge receipt to an input, so a point written just before a crash can be written
    again; InfluxDB then overwrites it with the same values."""
    def emit(text):
        sys.stdout.write(text)
        sys.stdout.flush()

    while True:
        if signal == "stdin":
            if not sys.stdin.readline():
                return 0                       # Telegraf closed our stdin: stop
        if drain:
            drain_all(b, max_windows)
        while b.lines(name, emit, limit=5000):
            pass
        if signal == "none":
            time.sleep(interval)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dsn")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install")
    r = sub.add_parser("register")
    for a in ("collection", "recording", "asset", "channel"):
        r.add_argument(a)
    r.add_argument("--window-samples", type=int, default=4096)
    d = sub.add_parser("drain")
    d.add_argument("source_id", type=int)
    d.add_argument("--max-windows", type=int, default=100)
    a = sub.add_parser("add-sink")
    a.add_argument("name")
    a.add_argument("kind", choices=("postgres", "line"))
    a.add_argument("target", nargs="?")
    a.add_argument("--measurement")
    a.add_argument("--tag", action="append", default=[], metavar="KEY=VALUE",
                   help="fixed tag on every point of a line sink, e.g. site=plant-a (repeatable)")
    for c in ("deliver", "lines"):
        x = sub.add_parser(c)
        x.add_argument("name")
        x.add_argument("--limit", type=int, default=1000)
    t = sub.add_parser("telegraf")
    t.add_argument("name")
    t.add_argument("--drain-all", action="store_true", help="publish new windows of every source first")
    t.add_argument("--signal", choices=("stdin", "none"), default="stdin")
    t.add_argument("--interval", type=float, default=10.0)
    t.add_argument("--max-windows", type=int, default=100)
    s = sub.add_parser("status")
    s.add_argument("source_id", type=int, nargs="?")
    s.add_argument("--sink")
    args = p.parse_args(argv)
    if args.cmd == "telegraf" and not args.interval > 0:
        p.error("--interval must be positive")
    if args.cmd == "add-sink" and args.kind == "postgres" and (args.measurement or args.tag):
        p.error("--measurement and --tag apply to line sinks only")
    if args.cmd == "status" and args.source_id is not None and args.sink:
        p.error("give a source ID or --sink, not both")

    with connect(args.dsn) as conn:
        b = Bridge(conn)
        if args.cmd == "install":
            b.install()
            out = "installed morlet_bridge schema 1"
        elif args.cmd == "register":
            with b.transaction():
                out = {"source_id": b.register(args.collection, args.recording, args.asset, args.channel,
                                               args.window_samples)}
        elif args.cmd == "drain":
            out = b.drain(args.source_id, args.max_windows)
        elif args.cmd == "add-sink":
            opts = {"measurement": args.measurement} if args.measurement else {}
            if args.tag:
                if any("=" not in t or t.startswith("=") for t in args.tag):
                    p.error("--tag takes KEY=VALUE with a nonempty key")
                pairs = [t.split("=", 1) for t in args.tag]
                if len({k for k, _ in pairs}) != len(pairs):
                    p.error("--tag: each key once")
                opts["tags"] = dict(pairs)
            if args.kind == "line" and args.target:
                p.error("a line sink takes no target table")
            out = {"sink_id": b.add_sink(args.name, args.kind, args.target, opts)}
        elif args.cmd == "deliver":
            out = {"delivered": b.deliver(args.name, args.limit)}
        elif args.cmd == "lines":
            n = b.lines(args.name, sys.stdout.write, args.limit)
            print(f"morlet-bridge: {n} points", file=sys.stderr)
            return 0
        elif args.cmd == "telegraf":
            return telegraf(b, args.name, args.drain_all, args.signal, args.interval, args.max_windows)
        else:
            out = b.sink_status(args.sink) if args.sink else b.status(args.source_id) if args.source_id is not None else \
                [b.status(r["source_id"]) for r in b.all("SELECT source_id FROM morlet_bridge.sources")]
        print(out if isinstance(out, str) else json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
