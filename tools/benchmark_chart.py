#!/usr/bin/env python3
"""Website benchmark chart: how many times MorletDB's figure each time-series database needs, per measure.

Every ratio comes from one session in which MorletDB 0.3.5-alpha.1 and that database ran alternately on the same
machine (evaluation/tsdb_b3/RESULTS.md: B3.8 for QuestDB, B3.9 for the others; summarize_b39.py prints the values),
never from two sessions. Speed: the other system's time over MorletDB's (p50), or MorletDB's throughput over the
other's, for the system's faster re-measured layout per measure. Storage: the other system's data on disk over
MorletDB's whole database (164.8 MiB); for QuestDB its native columns (dot) and its Parquet zstd 9 partitions (ring).
Memory: median peak server memory over the six loads of the system's lowest-memory layout, over MorletDB's in the
same pair. > 1 = MorletDB better.

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
    ("Data on disk", "storage", [(1162.7, 164.8, 232.7, "as Parquet zstd 9"), (1785.6, 164.8), (660.0, 164.8),
                                 (566.7, 164.8), (815.4, 164.8)], "MiB"),
    ("Ingestion, one connection", "ingest", [(6.302, 9.658), (6.328, 0.734), (6.257, 2.758), (6.243, 5.307),
                                             (6.303, 0.971)], "M samples/s"),
    ("Read 4,096 samples", "short", [(1.023, 0.630), (4.983, 0.625), (6.735, 0.631), (3.853, 0.641), (1.526, 0.643)],
     "ms p50"),
    ("Read 128,000 samples", "long", [(6.014, 6.886), (47.721, 6.721), (40.011, 6.721), (46.483, 6.789),
                                      (36.271, 6.712)], "ms p50"),
    ("Interval statistics", "stats", [(2.461, 2.545), (3.705, 2.566), (6.592, 2.540), (3.690, 2.530), (10.504, 2.551)],
     "ms p50"),
    # median peak server memory over a layout's loads, lowest-memory layout; JVM heaps follow their documented config
    ("Server memory", "memory", [(2301.4, 167.8), (6128.6, 167.9), (6021.1, 167.6), (1025.0, 167.7), (405.1, 167.7)],
     "MiB"),
]
LO, HI = 0.4, 50.0
X0, X1 = 230, 728
LANE, GAP = 12, 22


def xpos(r):
    return X0 + (math.log10(r) - math.log10(LO)) / (math.log10(HI) - math.log10(LO)) * (X1 - X0)


def tip(name, measure, a, b, unit):
    r = a / b
    if unit == "MiB":
        what = f"{a:,.0f} vs {b:,.0f} MiB"
    elif unit == "M samples/s":
        what = f"MorletDB {a:g} vs {b:g} M samples/s"
    else:
        what = f"{a:g} vs MorletDB {b:g} ms"
    side = "MorletDB better" if r > 1.05 else ("on par" if r >= 0.95 else f"{name.split()[0]} better")
    return f"{name}, {measure}: {r:.1f}× ({what}; {side})"


def svg():
    top = 76
    h = top + len(MEASURES) * (len(SYSTEMS) * LANE + GAP) + 44
    ink, muted, line = "var(--ink)", "var(--muted)", "var(--line)"
    out = [f'<svg class="bm-chart" viewBox="0 0 760 {h}" role="img" aria-labelledby="bm-chart-title bm-chart-desc" '
           f'font-family="inherit" font-size="12">',
           '<title id="bm-chart-title">How many times MorletDB\'s figure each database needs, per measure</title>',
           '<desc id="bm-chart-desc">Log scale. 1× is MorletDB; to the right MorletDB is better, to the left the other '
           'database. The tables in the tabs below list every value.</desc>']
    # legend
    x = 0
    for name, col in SYSTEMS:
        out.append(f'<circle cx="{x + 5}" cy="18" r="5" fill="{col}"/><text x="{x + 15}" y="22" fill="{ink}">{name}</text>')
        x += 15 + 7 * len(name) + 16
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
            if len(pr) == 4:   # a second form of the same system (QuestDB as Parquet): a ring on the same lane
                rx = xpos(pr[2] / b)
                out.append(f'<circle cx="{rx:.1f}" cy="{cy:.1f}" r="4" fill="var(--paper)" stroke="{col}" stroke-width="2">'
                           f'<title>{tip(name + " " + pr[3], label.lower(), pr[2], b, unit)}</title></circle>')
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="{col}" stroke="var(--paper)" stroke-width="2">'
                       f'<title>{tip(name, label.lower(), a, b, unit)}</title></circle>')
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
