import openpyxl, json, collections
rows = list(openpyxl.load_workbook('/w/data/toollife/metadata.xlsx', read_only=True).active.iter_rows(values_only=True))
L = {n.split("/")[1][:-4]: (u, c) for n, u, c in json.load(open("/w/iu2/zip_listing.json"))}
T = collections.OrderedDict()
for r in rows[1:]:
    e = T.setdefault(r[2], dict(type=r[3], adoc=r[4], rdoc=r[5], cycles=0, samples=set(), hard=set(), mb=0, first=r[0], files=[]))
    e["cycles"] += 1; e["samples"].add(r[1]); e["hard"].add(r[6]); e["mb"] += L[r[0]][0] / 1e6; e["files"].append((r[0], r[7], L[r[0]][0]))
print("tool type ADOC RDOC cycles samples hardness MB_total MB/cycle(first,last)")
for t, e in T.items():
    f = e["files"]
    print(t, e["type"], e["adoc"], e["rdoc"], e["cycles"], len(e["samples"]), f"{min(e['hard'])}-{max(e['hard'])}", round(e["mb"]), round(f[0][2] / 1e6), round(f[-1][2] / 1e6), "ctf first/last", f[0][1], f[-1][1], "order ok" if all(f[i][1] > f[i + 1][1] for i in range(len(f) - 1)) else "ORDER?")
print("tools", len(T), "cycles", sum(e["cycles"] for e in T.values()))
by = collections.Counter((e["type"], e["adoc"], e["rdoc"]) for e in T.values()); print("settings (type, ADOC, RDOC): tools", dict(by))
json.dump({str(t): dict(type=e["type"], adoc=e["adoc"], rdoc=e["rdoc"], files=e["files"]) for t, e in T.items()}, open("/w/iu2/tools.json", "w"))
