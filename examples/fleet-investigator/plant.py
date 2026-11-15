# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""The fictional plant: an ISA-95-structured context graph whose monitored bearings carry real IMS recordings.

Everything here is invented (the enterprise, sites, units, lots, suppliers, work orders), except the bearing product
named in the IMS readme and the IMS facts themselves: which test, which bearing, when, and how it failed. MODEL.md
maps every label and edge type to its ISA-95 concept. This module is data only; load.py writes it.

The mapping of IMS runs onto the plant (MODEL.md, "Linking"):
  - IMS test 1 (Oct-Nov 2003): blower F-212 at Site B, bearing positions 1-4 = IMS bearings 1-4 (channels 1, 3, 5,
    7: the first of each bearing's two accelerometers). The readme: inner race defect on bearing 3 and roller element
    defect on bearing 4 at the end of the test.
  - IMS test 2 (Feb 2004): pump P-102 at Site A, positions 1-4 = IMS bearings 1-4. The readme: outer race failure on
    bearing 1. P-102 was then overhauled and all four bearings replaced.
  - IMS test 3 (Mar-Apr 2004): the same pump P-102 with its new bearings, still running at the plant's "now"
    (NOW below). Snapshots from NOW on are held out and not loaded; the readme's outcome for this test (outer race
    failure on bearing 3) is not in the graph. It is the held-out answer the tests check the investigation against.
All other units have no waveform monitoring: they are in the graph with their bearings, lots and work orders only.
"""

GRAPH = "plant"
COLLECTION = "fleet.vibration"
NOW = "2004-04-17T12:00:00Z"        # the plant's present: IMS test 3 snapshots at or after this are not loaded
RATE = 20000.0
SNAPSHOT_EVERY = 6                  # every 6th IMS file (one an hour on the 10-minute schedule)
BEARING_PRODUCT = "Rexnord ZA-2115 double-row bearing"   # as named in the IMS readme
SHARED = "*"                        # site value of enterprise-level nodes (visible to every role with any site)

# IMS runs as distributed (folder of the extracted archive), the unit and the columns used per bearing position.
IMS_RUNS = {
    "1": {"folder": "1st_test", "files": 2156, "columns": 8, "unit": "F-212", "use": (0, 2, 4, 6)},
    "2": {"folder": "2nd_test", "files": 984, "columns": 4, "unit": "P-102", "use": (0, 1, 2, 3)},
    "3": {"folder": "4th_test/txt", "files": 6324, "columns": 4, "unit": "P-102", "use": (0, 1, 2, 3)},
}

ENTERPRISE = {"id": "AW", "name": "Aldermere Water (fictional)", "site": SHARED}
SITES = [
    {"id": "AW-N", "name": "North works (fictional)", "site": "A"},
    {"id": "AW-S", "name": "South works (fictional)", "site": "B"},
]
AREAS = [
    {"id": "AW-N-A1", "name": "Raw water pumping", "site": "A", "parent": "AW-N"},
    {"id": "AW-N-A2", "name": "Aeration", "site": "A", "parent": "AW-N"},
    {"id": "AW-S-B1", "name": "Treated water pumping", "site": "B", "parent": "AW-S"},
    {"id": "AW-S-B2", "name": "Aeration", "site": "B", "parent": "AW-S"},
]
PROCESS_CELLS = [
    {"id": "AW-N-A1-PS1", "name": "Intake pump station", "site": "A", "parent": "AW-N-A1"},
    {"id": "AW-N-A2-BH1", "name": "Blower hall", "site": "A", "parent": "AW-N-A2"},
    {"id": "AW-S-B1-PS1", "name": "High-lift pump station", "site": "B", "parent": "AW-S-B1"},
    {"id": "AW-S-B2-BH1", "name": "Blower hall", "site": "B", "parent": "AW-S-B2"},
]
EQUIPMENT_CLASSES = [
    {"id": "EC-PUMP", "name": "Centrifugal pump set, belt-driven line shaft", "site": SHARED},
    {"id": "EC-BLOWER", "name": "Aeration blower, belt-driven line shaft", "site": SHARED},
    {"id": "EC-MOTOR", "name": "AC induction motor", "site": SHARED},
    {"id": "EC-SHAFT", "name": "Line shaft", "site": SHARED},
    {"id": "EC-BRGPOS", "name": "Bearing position (housing with accelerometer)", "site": SHARED},
]


def _units():
    out = []
    for cell, prefix, numbers, cls in (("AW-N-A1-PS1", "P", (101, 102, 103, 104), "EC-PUMP"),
                                       ("AW-N-A2-BH1", "F", (111, 112, 113, 114), "EC-BLOWER"),
                                       ("AW-S-B1-PS1", "P", (201, 202, 203, 204), "EC-PUMP"),
                                       ("AW-S-B2-BH1", "F", (211, 212, 213, 214), "EC-BLOWER")):
        site = "A" if cell.startswith("AW-N") else "B"
        for n in numbers:
            out.append({"id": f"{prefix}-{n}", "name": f"{'Pump' if prefix == 'P' else 'Blower'} {prefix}-{n}",
                        "site": site, "parent": cell, "class": cls,
                        "shaft_rpm": 2000, "radial_load_lbs": 6000})
    return out


UNITS = _units()
MONITORED_UNITS = {"P-102", "F-212"}
POSITIONS = ("BRG-1", "BRG-2", "BRG-3", "BRG-4")


def equipment_id(unit, position):
    """The ISA-95 equipment ID of a bearing position, as carried in recording metadata."""
    return f"{unit}.{position}"


def _modules():
    out = []
    for u in UNITS:
        out.append({"id": f"{u['id']}.MTR", "name": "Drive motor", "kind": "motor", "site": u["site"],
                    "parent": u["id"], "class": "EC-MOTOR"})
        out.append({"id": f"{u['id']}.SHAFT", "name": "Line shaft", "kind": "shaft", "site": u["site"],
                    "parent": u["id"], "class": "EC-SHAFT"})
        for p in POSITIONS:
            m = {"id": equipment_id(u["id"], p), "name": f"Bearing position {p[-1]}", "kind": "bearing_position",
                 "position": p, "site": u["site"], "parent": f"{u['id']}.SHAFT", "class": "EC-BRGPOS",
                 "equipment_id": equipment_id(u["id"], p), "monitored": u["id"] in MONITORED_UNITS}
            if m["monitored"]:
                m["collection"] = COLLECTION
            out.append(m)
    return out


EQUIPMENT_MODULES = _modules()

MATERIAL_CLASS = {"id": "MC-RB", "name": "Rolling-element bearings", "site": SHARED}
MATERIAL_DEFINITION = {"id": "MD-ZA2115", "name": BEARING_PRODUCT, "site": SHARED,
                       "note": "Product as named in the IMS bearing data readme; the only real product name used"}
SUPPLIERS = [
    {"id": "SUP-1", "name": "Distributor 1 (fictional)", "site": SHARED},
    {"id": "SUP-2", "name": "Distributor 2 (fictional)", "site": SHARED},
]
LOTS = [   # quantity = bearings of the lot in this graph
    {"id": "LOT-0207", "received": "2002-07-15", "supplier": "SUP-2"},
    {"id": "LOT-0301", "received": "2003-01-20", "supplier": "SUP-1"},
    {"id": "LOT-0305", "received": "2003-05-06", "supplier": "SUP-2"},
    {"id": "LOT-0306", "received": "2003-06-12", "supplier": "SUP-1"},
    {"id": "LOT-0310", "received": "2003-10-30", "supplier": "SUP-1"},
    {"id": "LOT-0311", "received": "2003-11-18", "supplier": "SUP-1"},
    {"id": "LOT-0402", "received": "2004-02-24", "supplier": "SUP-2"},
    {"id": "LOT-0405", "received": "2004-04-02", "supplier": "SUP-1"},
]
for _lot in LOTS:
    _lot["site"] = SHARED

FAILURE_MODES = [
    {"id": "FM-BRG", "name": "Rolling bearing failure", "parent": None},
    {"id": "FM-IR", "name": "Inner race defect", "parent": "FM-BRG"},
    {"id": "FM-OR", "name": "Outer race defect", "parent": "FM-BRG"},
    {"id": "FM-RE", "name": "Rolling element (roller) defect", "parent": "FM-BRG"},
]
for _fm in FAILURE_MODES:
    _fm["site"] = SHARED

PERSONNEL_CLASSES = [
    {"id": "PC-RELENG", "name": "Reliability engineer", "site": SHARED},
    {"id": "PC-MTECH", "name": "Maintenance technician", "site": SHARED},
    {"id": "PC-CONTRACT", "name": "Contract maintenance technician", "site": SHARED},
]
OPERATIONS_SEGMENTS = [
    {"id": "SEG-CONT", "name": "Continuous duty, constant speed and load", "site": SHARED},
]
STORES = [
    {"id": "AW-N-STORE", "name": "North works stores", "site": "A"},
    {"id": "AW-S-STORE", "name": "South works stores", "site": "B"},
]

# IMS run windows on the rig clock (file names; time zone not stated, shown as UTC), from the extracted files.
IMS_TIMES = {"1": ("2003-10-22T12:06:24Z", "2003-11-25T23:39:56Z"),
             "2": ("2004-02-12T10:32:39Z", "2004-02-19T06:22:39Z"),
             "3": ("2004-03-04T09:27:46Z", "2004-04-18T02:42:55Z")}


def _serial(lot, n):
    return f"SN-{lot[4:]}-{n:03d}"


def _physical_assets():
    """Bearings (ISA-95 physical assets) and their install periods (INSTALLED_IN {from, to})."""
    assets, installs, stored = [], [], []
    counters = {}

    def new(lot, site, **extra):
        counters[lot] = counters.get(lot, 0) + 1
        a = {"id": _serial(lot, counters[lot]), "serial": _serial(lot, counters[lot]), "lot": lot, "site": site,
             "material_definition": MATERIAL_DEFINITION["id"], **extra}
        assets.append(a)
        return a

    def install(asset, unit, position, start, end=None, site=None):
        e = {"asset": asset["id"], "module": equipment_id(unit, position), "from": start, "site": site or asset["site"]}
        if end:
            e["to"] = end
        installs.append(e)

    default_lot = {"P-101": "LOT-0301", "P-103": "LOT-0301", "P-104": "LOT-0301",
                   "F-111": "LOT-0207", "F-112": "LOT-0207", "F-113": "LOT-0207", "F-114": "LOT-0207",
                   "P-201": "LOT-0305", "P-202": "LOT-0305", "P-203": "LOT-0305", "P-204": "LOT-0305",
                   "F-211": "LOT-0207", "F-213": "LOT-0305", "F-214": "LOT-0305"}
    commissioned = {"LOT-0207": "2002-09-02T08:00:00Z", "LOT-0301": "2003-02-10T08:00:00Z",
                    "LOT-0305": "2003-05-19T08:00:00Z"}
    # Routine single-bearing replacements with LOT-0402 (work orders below): unit, position, date.
    later = {("P-104", "BRG-2"): "2004-03-10T10:00:00Z", ("F-113", "BRG-1"): "2004-03-22T09:00:00Z",
             ("P-203", "BRG-4"): "2004-03-15T13:00:00Z", ("F-214", "BRG-3"): "2004-04-02T08:30:00Z"}
    units = {u["id"]: u for u in UNITS}
    for uid, lot in default_lot.items():
        site = units[uid]["site"]
        for p in POSITIONS:
            a = new(lot, site)
            if (uid, p) in later:
                install(a, uid, p, commissioned[lot], later[(uid, p)])
                a["status"] = "removed"
                b = new("LOT-0402", site, status="installed")
                install(b, uid, p, later[(uid, p)])
            else:
                a["status"] = "installed"
                install(a, uid, p, commissioned[lot])

    # F-212 at Site B: IMS test 1, then an overhaul with LOT-0310.
    for i, p in enumerate(POSITIONS):
        a = new("LOT-0306", "B", status="removed", ims_test="1", ims_bearing=str(i + 1))
        install(a, "F-212", p, "2003-10-22T11:00:00Z", "2003-11-26T10:00:00Z")
    for p in POSITIONS:
        install(new("LOT-0310", "B", status="installed"), "F-212", p, "2003-11-28T14:00:00Z")
    # P-102 at Site A: IMS test 2, overhaul, IMS test 3 (current).
    for i, p in enumerate(POSITIONS):
        a = new("LOT-0311", "A", status="removed", ims_test="2", ims_bearing=str(i + 1))
        install(a, "P-102", p, "2004-02-12T09:00:00Z", "2004-02-19T08:00:00Z")
    for i, p in enumerate(POSITIONS):
        a = new("LOT-0402", "A", status="installed", ims_test="3", ims_bearing=str(i + 1))
        install(a, "P-102", p, "2004-03-04T09:00:00Z")
    # Spares on the shelf.
    for lot, store, site in (("LOT-0402", "AW-N-STORE", "A"), ("LOT-0402", "AW-N-STORE", "A"),
                             ("LOT-0301", "AW-N-STORE", "A"), ("LOT-0405", "AW-S-STORE", "B"),
                             ("LOT-0405", "AW-S-STORE", "B"), ("LOT-0405", "AW-S-STORE", "B")):
        a = new(lot, site, status="spare")
        stored.append({"asset": a["id"], "store": store, "site": site, "from": "2004-04-05T08:00:00Z"})
    return assets, installs, stored


PHYSICAL_ASSETS, INSTALLS, STORED = _physical_assets()
for _lot in LOTS:
    _lot["quantity"] = sum(1 for a in PHYSICAL_ASSETS if a["lot"] == _lot["id"])

SPARE_PARTS = [
    {"id": "SP-AW-N-ZA2115", "store": "AW-N-STORE", "site": "A", "reorder_point": 2,
     "on_hand": sum(1 for s in STORED if s["store"] == "AW-N-STORE")},
    {"id": "SP-AW-S-ZA2115", "store": "AW-S-STORE", "site": "B", "reorder_point": 2,
     "on_hand": sum(1 for s in STORED if s["store"] == "AW-S-STORE")},
]


def asset_of(ims_test, ims_bearing):
    """The serial of the bearing that ran as IMS bearing `ims_bearing` in IMS test `ims_test`."""
    return next(a["id"] for a in PHYSICAL_ASSETS
                if a.get("ims_test") == ims_test and a.get("ims_bearing") == ims_bearing)


# Failures as recorded in the IMS readme (the only failures in the graph that come from the data), each with its
# evidence recording: the bearing position's last snapshot of the test.
IMS_README = "IMS bearing data readme (Readme Document for IMS Bearing Data.pdf, in the NASA archive)"
FAILURES = [
    {"id": "FL-2003-11-F212-BRG3", "asset": asset_of("1", "3"), "mode": "FM-IR", "site": "B",
     "detected": "2003-11-25T23:39:56Z", "source": IMS_README + ", set no. 1: inner race defect in bearing 3",
     "evidence_test": "1", "evidence_bearing": "3"},
    {"id": "FL-2003-11-F212-BRG4", "asset": asset_of("1", "4"), "mode": "FM-RE", "site": "B",
     "detected": "2003-11-25T23:39:56Z", "source": IMS_README + ", set no. 1: roller element defect in bearing 4",
     "evidence_test": "1", "evidence_bearing": "4"},
    {"id": "FL-2004-02-P102-BRG1", "asset": asset_of("2", "1"), "mode": "FM-OR", "site": "A",
     "detected": "2004-02-19T06:22:39Z", "source": IMS_README + ", set no. 2: outer race failure in bearing 1",
     "evidence_test": "2", "evidence_bearing": "1"},
]

# Maintenance (ISA-95 Part 3): request -> work order -> response. The two IMS failures get corrective work; four
# routine replacements put LOT-0402 bearings into other units.
MAINTENANCE = [
    {"request": {"id": "MR-2003-1126-01", "requested": "2003-11-26T07:30:00Z", "site": "B", "module": "F-212.BRG-3",
                 "reason": "Vibration and noise on bearing positions 3 and 4; unit stopped"},
     "order": {"id": "WO-2003-1126-01", "kind": "corrective", "opened": "2003-11-26T08:00:00Z",
               "closed": "2003-11-28T16:00:00Z", "site": "B", "unit": "F-212", "personnel": "PC-MTECH",
               "task": "Replace bearings in all four positions (overhaul); inspect removed bearings",
               "consumed_lot": "LOT-0310", "consumed": 4},
     "response": {"id": "RSP-2003-1128-01", "completed": "2003-11-28T16:00:00Z", "site": "B",
                  "findings": "Inner race defect on position 3 and roller defect on position 4 found on removal",
                  "outcome": "Returned to service 2003-11-28 with LOT-0310 bearings; no recurrence recorded",
                  "failures": ["FL-2003-11-F212-BRG3", "FL-2003-11-F212-BRG4"]}},
    {"request": {"id": "MR-2004-0219-01", "requested": "2004-02-19T07:00:00Z", "site": "A", "module": "P-102.BRG-1",
                 "reason": "High vibration on bearing position 1; unit stopped"},
     "order": {"id": "WO-2004-0219-01", "kind": "corrective", "opened": "2004-02-19T07:30:00Z",
               "closed": "2004-03-04T08:30:00Z", "site": "A", "unit": "P-102", "personnel": "PC-MTECH",
               "task": "Replace bearings in all four positions (overhaul); inspect removed bearings",
               "consumed_lot": "LOT-0402", "consumed": 4},
     "response": {"id": "RSP-2004-0304-01", "completed": "2004-03-04T08:30:00Z", "site": "A",
                  "findings": "Outer race failure on position 1 found on removal",
                  "outcome": "Returned to service 2004-03-04 with LOT-0402 bearings after waiting for parts",
                  "failures": ["FL-2004-02-P102-BRG1"]}},
]
for (_unit, _pos), _when in {("P-104", "BRG-2"): "2004-03-10T10:00:00Z", ("F-113", "BRG-1"): "2004-03-22T09:00:00Z",
                             ("P-203", "BRG-4"): "2004-03-15T13:00:00Z",
                             ("F-214", "BRG-3"): "2004-04-02T08:30:00Z"}.items():
    _site = next(u["site"] for u in UNITS if u["id"] == _unit)
    _d = _when[:10].replace("-", "")[2:]
    MAINTENANCE.append({
        "request": {"id": f"MR-{_d}-{_unit}", "requested": _when[:11] + "06:00:00Z", "site": _site,
                    "module": equipment_id(_unit, _pos), "reason": "Planned: bearing replacement during seal overhaul"},
        "order": {"id": f"WO-{_d}-{_unit}", "kind": "preventive", "opened": _when[:11] + "06:30:00Z", "closed": _when,
                  "site": _site, "unit": _unit, "personnel": "PC-CONTRACT" if _site == "A" else "PC-MTECH",
                  "task": f"Replace bearing in position {_pos[-1]} during seal overhaul",
                  "consumed_lot": "LOT-0402", "consumed": 1},
        "response": {"id": f"RSP-{_d}-{_unit}", "completed": _when, "site": _site,
                     "findings": "Removed bearing in serviceable condition", "outcome": "Returned to service",
                     "failures": []}})

# Operating context (ISA-95 operations: job orders on an operations segment), mirrored into MorletDB context.
JOB_ORDERS = [
    {"id": "JO-F212-2003-10", "unit": "F-212", "site": "B", "segment": "SEG-CONT", "ims_test": "1",
     "from": "2003-10-22T11:00:00Z", "to": "2003-11-26T07:30:00Z", "shaft_rpm": 2000, "radial_load_lbs": 6000},
    {"id": "JO-P102-2004-02", "unit": "P-102", "site": "A", "segment": "SEG-CONT", "ims_test": "2",
     "from": "2004-02-12T09:00:00Z", "to": "2004-02-19T07:00:00Z", "shaft_rpm": 2000, "radial_load_lbs": 6000},
    {"id": "JO-P102-2004-03", "unit": "P-102", "site": "A", "segment": "SEG-CONT", "ims_test": "3",
     "from": "2004-03-04T09:00:00Z", "shaft_rpm": 2000, "radial_load_lbs": 6000},
]


def job_order_for(ims_test):
    return next(j for j in JOB_ORDERS if j["ims_test"] == ims_test)


def site_of(unit):
    return next(u["site"] for u in UNITS if u["id"] == unit)
