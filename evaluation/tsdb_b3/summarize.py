#!/usr/bin/env python3
"""B3 tables from results/*.json (markdown on stdout, summary.json beside the inputs)."""
import json

import numpy as np

import common

R = common.RESULTS
MiB = 2 ** 20


def j(name):
    p = R / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def q_bytes(st, group, rows=0):
    t = st["tables"].get(f"b3_{group}", {})
    get = lambda c: t.get(c, {}).get("allocated", 0)
    ident = get("identity")
    sym = get("symbols")
    if not ident and rows and sym >= 4 * rows:      # load 1 predates the split: the key file is 4 bytes per row
        ident, sym = 4 * rows, sym - 4 * rows
    return {"data": get("data") + get("timestamps") + ident, "timestamps": get("timestamps"), "identity": ident,
            "symbols_metadata": sym + get("metadata"), "wal": get("wal")}


def main():
    m = common.manifest()
    canon = m["canonical_bytes"]
    total_canon = sum(canon.values())
    out = {"canonical_bytes": canon}
    lines = []

    # storage
    lines.append("### A. Storage (allocated bytes after a settled state; D2 load 1, checked on load 2)\n")
    lines.append("| Arm | Data | Indexes, symbols, metadata | WAL / journal | Data bytes per sample | Data / float64 |")
    lines.append("|---|---|---|---|---|---|")
    rows = {}
    q1, q2 = j("load_questdb_D2_r1"), j("load_questdb_D2_r2")
    for label, res, key in (("Q-native", q1, "storage_native"), ("Q-parquet-lz4", q1, "storage_parquet_LZ4_RAW"),
                            ("Q-parquet-zstd9", q2, "storage_parquet_ZSTD")):
        if res and key in res:
            nrows = {g: sum(a["channels"][s["channels"][0]]["samples"] for a in m["acquisitions"])
                     for g, s in m["groups"].items()}
            parts = [q_bytes(res[key], g, nrows[g] if key == "storage_native" else 0) for g in ("high", "mid", "process")]
            d = sum(p["data"] for p in parts)
            ix = sum(p["symbols_metadata"] for p in parts)
            w = sum(p["wal"] for p in parts)
            rows[label] = {"data": d, "index_meta": ix, "wal": w,
                           "high_data": parts[0]["data"], "high_timestamps": parts[0]["timestamps"]}
    for label, name in (("M-base", "load_morletdb_D2_r1"), ("M-search", "load_morletdb-search_D2_r1")):
        res = j(name)
        if res and "storage" in res:
            rel = res["storage"]["relations"]
            d = sum(v["heap"] + v["toast"] for k, v in rel.items() if k.endswith("_chunks"))
            ix = sum(v["indexes"] for v in rel.values()) + sum(v["heap"] + v["toast"] for k, v in rel.items()
                                                               if not k.endswith("_chunks"))
            rows[label] = {"data": d, "index_meta": ix, "wal": res["storage"]["volume"]["pg_wal_allocated"],
                           "relations": rel}
    for label, r in rows.items():
        lines.append(f"| {label} | {r['data'] / MiB:,.1f} MiB | {r['index_meta'] / MiB:,.2f} MiB | {r['wal'] / MiB:,.1f} MiB "
                     f"| {8 * r['data'] / total_canon:.3f} | {r['data'] / total_canon:.3f} |")
    pk = j("packed_parquet")
    ex = (j("load_morletdb_D2_r1") or {}).get("exchange_file")
    if ex:
        lines.append(f"| Packed: MorletDB exchange file | {ex['bytes'] / MiB:,.1f} MiB | | | {8 * ex['bytes'] / total_canon:.3f} "
                     f"| {ex['bytes'] / total_canon:.3f} |")
        rows["packed-exchange"] = {"data": ex["bytes"]}
    if pk:
        b = sum(g["bytes"] for g in pk["groups"].values())
        lines.append(f"| Packed: Parquet per group, zstd 9 | {b / MiB:,.1f} MiB | | | {8 * b / total_canon:.3f} | {b / total_canon:.3f} |")
        rows["packed-parquet"] = {"data": b, "groups": pk["groups"]}
    lines.append(f"\nCanonical float64 size: {total_canon / MiB:,.1f} MiB ({sum(m['samples'].values()):,} samples).\n")
    out["storage"] = rows

    # ingestion
    lines.append("### B. Ingestion (three fresh loads per class; median of the three, range in brackets)\n")
    lines.append("| System | Class | Samples/s | Wall s | Client conversion s | Ack per acquisition p50 / p95 ms "
                 "| Queryable after last ack s | Server CPU mean % | Peak memory MiB |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    ing = {}
    for sysname in ("questdb", "morletdb"):
        for d in ("D1", "D2"):
            rs = [j(f"load_{sysname}_{d}_r{i}") for i in (1, 2, 3)]
            rs = [r for r in rs if r and r.get("correct")]
            if not rs:
                continue
            f = lambda key: [r["ingest"][key] for r in rs]
            sps = f("samples_per_s")
            ing[f"{sysname}/{d}"] = {"samples_per_s": sps, "loads": len(rs),
                                     "ack_p50": [r["ingest"]["ack_per_acquisition"]["p50_ms"] for r in rs],
                                     "ack_p95": [r["ingest"]["ack_per_acquisition"]["p95_ms"] for r in rs]}
            med = lambda xs: float(np.median(xs))
            lines.append(
                f"| {sysname} | {d} | {med(sps) / 1e6:.2f} M [{min(sps) / 1e6:.2f}–{max(sps) / 1e6:.2f}] | {med(f('wall_s')):.1f} "
                f"| {med(f('conversion_s')):.1f} | {med(ing[f'{sysname}/{d}']['ack_p50']):.0f} / "
                f"{med(ing[f'{sysname}/{d}']['ack_p95']):.0f} | {med(f('queryable_after_last_ack_s')):.3f} "
                f"| {med([r['ingest']['server'].get('cpu_percent_mean', 0) for r in rs]):.0f} "
                f"| {med([r['ingest']['server'].get('memory_peak_mib', 0) for r in rs]):,.0f} |")
    out["ingestion"] = ing

    # reads
    lines.append("\n### C. Exact interval reads (D2 load 1, warm, end to end in the client; p50 / p95 ms)\n")
    q1r = (q1 or {}).get("reads_native")
    q2r = (q2 or {}).get("reads_parquet_ZSTD")
    m1r = (j("load_morletdb_D2_r1") or {}).get("reads")
    lines.append("| Query | Samples | Q-native | Q-parquet-zstd9 | M-base |")
    lines.append("|---|---|---|---|---|")
    for rq in ("R1", "R2", "R3", "R4_recent", "R4_older", "R5"):
        cell = lambda r: (f"{r[rq]['p50_ms']:.2f} / {r[rq]['p95_ms']:.2f}" + (f" ({r[rq]['queries_per_s']:.0f} q/s)"
                          if "queries_per_s" in r[rq] else "")) if r and rq in r else "–"
        n = (m1r or q1r or {}).get(rq, {}).get("samples_per_query", 4096)
        lines.append(f"| {rq} | {n:,} | {cell(q1r)} | {cell(q2r)} | {cell(m1r)} |")
    out["reads"] = {"Q-native": q1r, "Q-parquet-zstd9": q2r, "M-base": m1r}

    lines.append("\n### D. Interval analytics (min, max, mean, RMS over the R2 intervals; p50 / p95 ms)\n")
    lines.append("| Arm | p50 / p95 ms | Max relative difference to numpy |")
    lines.append("|---|---|---|")
    an = {"Q-native": (q1 or {}).get("analytics"), "Q-parquet-zstd9": (q2 or {}).get("analytics_parquet_ZSTD"),
          "M-base": (j("load_morletdb_D2_r1") or {}).get("analytics")}
    for k, a in an.items():
        if a:
            lines.append(f"| {k} | {a['p50_ms']:.2f} / {a['p95_ms']:.2f} | {a['max_relative_difference']:.1e} |")
    out["analytics"] = an
    (R / "summary.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
