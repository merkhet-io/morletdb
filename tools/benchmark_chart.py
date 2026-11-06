#!/usr/bin/env python3
"""Website benchmark chart: how many times MorletDB's figure each time-series database needs, per measure.

Every speed and memory ratio comes from one session (B3.13, evaluation/tsdb_b3/RESULTS.md) in which the released
MorletDB 0.3.6-alpha.1 and that database ran alternately on the same machine, reads from a fresh client process
(summarize_b313.py prints the values), never from two sessions. Speed: the other system's time over MorletDB's (p50),
or MorletDB's throughput over the other's, for the system's faster layout per measure. Storage (deterministic, from
each system's stage) is the other system's data on disk over MorletDB 0.3.6's whole database (161.5 MiB, B3.13); for
QuestDB its native columns (dot) and its Parquet zstd 9 partitions (ring). Interval statistics on QuestDB's lane:
the ring is MorletDB with chunk summaries (B3.13's second MorletDB arm). Memory: median peak server memory over a
system's loads, lowest-memory layout, over MorletDB's in the same pair. > 1 = MorletDB better.

  python3 tools/benchmark_chart.py           # rewrite the chart between its markers in docs/index.html
  python3 tools/benchmark_chart.py --check   # fail if the chart in the page differs
"""
import math
import sys
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "docs" / "index.html"
START, END = "<!-- benchmark-chart:start -->", "<!-- benchmark-chart:end -->"
SYSTEMS = [("QuestDB 10.1", "#3987e5"), ("CrateDB 6.4", "#d95926"), ("IoTDB 2.0", "#199e70"), ("TDengine 3.4", "#c98500"), ("TimescaleDB 2.31", "#d55181")]
# measure: [(numerator, denominator) per system, in SYSTEMS order] - the measured values, not rounded ratios
MEASURES = [
    ("Data on disk", "storage", [(1162.7, 161.5, 232.7, "as Parquet zstd 9"), (1785.6, 161.5), (660.0, 161.5),
                                 (566.7, 161.5), (815.4, 161.5)], "MiB"),
    ("Ingestion, one connection", "ingest", [(14.13, 9.49), (11.60, 0.68), (13.92, 2.69), (14.28, 4.79),
                                             (13.84, 0.90)], "M samples/s"),
    ("Read 4,096 samples", "short", [(1.10, 0.65), (7.16, 0.65), (8.51, 0.73), (3.65, 0.62), (1.55, 0.65)],
     "ms p50"),
    ("Read 128,000 samples", "long", [(7.17, 7.58), (54.33, 7.97), (42.44, 8.91), (48.75, 6.72),
                                      (40.54, 8.16)], "ms p50"),
    ("Interval statistics", "stats", [(2.59, 2.54), (5.06, 3.04), (7.49, 2.63), (3.74, 2.52), (10.16, 2.62)],
     "ms p50"),
    # median peak server memory over a layout's loads, lowest-memory layout; JVM heaps follow their documented config
    ("Server memory", "memory", [(2304, 171), (6166, 176), (5925, 173), (1024, 168), (400, 177)], "MiB"),
]
# a dot that is not the released build's base configuration, and a ring with its own pair: (measure, system) -> ...
DOT_NOTE = {}
RING = {("stats", 0): (2.59, 0.56, "with chunk summaries, B3.13")}
LO, HI = 0.4, 50.0
X0, X1 = 230, 728
LANE, GAP = 12, 22


def xpos(r):
    return X0 + (math.log10(r) - math.log10(LO)) / (math.log10(HI) - math.log10(LO)) * (X1 - X0)


def tip(name, measure, a, b, unit):
    r = a / b
    if unit == "MiB":
        what = f"{a:,.0f} vs {b:,.1f} MiB"   # MorletDB to one decimal (161.5): the tables say 161, not 162
    elif unit == "M samples/s":
        what = f"MorletDB {a:g} vs {b:g} M samples/s"
    else:
        what = f"{a:g} vs MorletDB {b:g} ms"
    side = "MorletDB better" if r > 1.05 else ("on par" if r >= 0.95 else f"{name.split()[0]} better")
    return f"{name}, {measure}: {r:.1f}× ({what}; {side})"


def svg():
    top = 96
    h = top + len(MEASURES) * (len(SYSTEMS) * LANE + GAP) + 44
    ink, muted, line = "var(--ink)", "var(--muted)", "var(--line)"
    out = [f'<svg class="bm-chart" viewBox="0 0 760 {h}" role="img" aria-label="How many times MorletDB&#39;s figure each database needs, per measure" '
           f'aria-describedby="bm-chart-desc" font-family="inherit" font-size="12">',
           # no <title> on the svg: browsers show it as a native tooltip over the per-dot hover tooltips
           '<desc id="bm-chart-desc">Log scale. 1× is MorletDB; to the right MorletDB is better, to the left the other '
           'database. The tables in the tabs below list every value.</desc>']
    # legend
    x = 0
    for name, col in SYSTEMS:
        out.append(f'<circle cx="{x + 5}" cy="18" r="5" fill="{col}"/><text x="{x + 15}" y="22" fill="{ink}">{name}</text>')
        x += 15 + 7 * len(name) + 16
    # the hollow ring: the same database in a second form, on its own line
    out.append(f'<circle cx="5" cy="42" r="4" fill="var(--paper)" stroke="{SYSTEMS[0][1]}" stroke-width="2"/>'
               f'<text x="15" y="46" fill="{ink}">Hollow ring: QuestDB as Parquet zstd 9 (data on disk); MorletDB with '
               f'chunk summaries (interval statistics)</text>')
    # axis and grid
    y_axis = h - 34
    for t, lab in [(0.5, "0.5×"), (1, "1×"), (2, "2×"), (5, "5×"), (10, "10×"), (20, "20×"), (50, "50×")]:
        xx = xpos(t)
        w = "1.5" if t == 1 else "1"
        col = ink if t == 1 else line
        out.append(f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{top - 14}" y2="{y_axis}" stroke="{col}" stroke-width="{w}"'
                   f'{"" if t == 1 else " stroke-dasharray=\"2 4\""}/>')
        out.append(f'<text x="{xx:.1f}" y="{y_axis + 16}" text-anchor="middle" fill="{muted}">{lab}</text>')
    out.append(f'<text x="{xpos(1) + 6:.1f}" y="{top - 20}" fill="{ink}" font-weight="600">MorletDB = 1×</text>')
    out.append(f'<text x="{X1}" y="{top - 20}" text-anchor="end" fill="{muted}">MorletDB better →</text>')
    out.append(f'<text x="{X0}" y="{top - 20}" fill="{muted}">← other better</text>')
    y = top
    for label, key, pairs, unit in MEASURES:
        rows = len(SYSTEMS) * LANE
        out.append(f'<text x="0" y="{y + rows / 2 + 4:.1f}" fill="{ink}">{label}</text>')
        out.append(f'<line x1="{X0}" x2="{X1}" y1="{y + rows + GAP / 2:.1f}" y2="{y + rows + GAP / 2:.1f}" stroke="{line}"/>')
        for i, ((name, col), pr) in enumerate(zip(SYSTEMS, pairs)):
            a, b = pr[:2]
            r = a / b
            cy = y + i * LANE + LANE / 2
            cx = xpos(r)
            out.append(f'<line x1="{xpos(1):.1f}" x2="{cx:.1f}" y1="{cy:.1f}" y2="{cy:.1f}" stroke="{col}" '
                       f'stroke-width="2" stroke-opacity="0.45"/>')
            ring = None
            if len(pr) == 4:   # a second form of the same system (QuestDB as Parquet): a ring on the same lane
                ring = (pr[2], b, name + " " + pr[3])
            elif (key, i) in RING:   # the same system against another MorletDB build (its own session)
                ra, rb, note = RING[(key, i)]
                ring = (ra, rb, f"{name} vs MorletDB {note}")
            if ring:
                rx = xpos(ring[0] / ring[1])
                out.append(f'<circle cx="{rx:.1f}" cy="{cy:.1f}" r="4" fill="var(--paper)" stroke="{col}" stroke-width="2">'
                           f'<title>{tip(ring[2], label.lower(), ring[0], ring[1], unit)}</title></circle>')
            who = f"{name} vs MorletDB {DOT_NOTE[(key, i)]}" if (key, i) in DOT_NOTE else name
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="{col}" stroke="var(--paper)" stroke-width="2">'
                       f'<title>{tip(who, label.lower(), a, b, unit)}</title></circle>')
        y += rows + GAP
    out.append("</svg>")
    return "".join(out)


def main():
    s = PAGE.read_text()
    i, j = s.find(START), s.find(END)
    if i < 0 or j < 0:
        sys.exit("markers not found")
    new = s[:i + len(START)] + svg() + s[j:]
    if "--check" in sys.argv:
        if new != s:
            sys.exit("benchmark chart out of date: run python3 tools/benchmark_chart.py")
        print("benchmark chart up to date")
        return
    PAGE.write_text(new)
    print("benchmark chart written")


if __name__ == "__main__":
    main()
