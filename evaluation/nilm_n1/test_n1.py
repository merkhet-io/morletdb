"""Tests of the N1 harness on synthetic data (no PLAID file is read)."""
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import n1  # noqa: E402


def mains(cycles, amp, f=60.0):
    t = np.arange(cycles * n1.CYCLE) / n1.RATE
    return amp * np.sin(2 * np.pi * f * t)


def test_onset_finds_the_switching_cycle_and_keeps_ten_cycles_before():
    x = np.concatenate([mains(20, 0.01), mains(40, 2.0)])
    start, flagged = n1.onset(x)
    assert not flagged and start == 20 * n1.CYCLE - n1.PRE


def test_onset_clamps_to_the_record():
    early = np.concatenate([mains(3, 0.0), mains(57, 1.0)])
    assert n1.onset(early) == (0, False)
    late = np.concatenate([mains(50, 0.0), mains(10, 1.0)])
    start, _ = n1.onset(late)
    assert start == len(late) - n1.WINDOW


def test_no_clear_transient_is_flagged_at_zero():
    assert n1.onset(mains(60, 1.0) + 0.001) == (0, True)


def test_samples_are_the_stored_float64_values():
    x = np.array([-0.26, 163.89, 0.12300000000000001, -0.0, 1e-19])
    assert n1.samples(x).tobytes() == x.tobytes()


def test_feature_neighbours_never_use_the_query_location():
    rng = np.random.default_rng(0)
    F = rng.normal(size=(30, 4))
    loc = np.array([f"h{i % 6}" for i in range(30)])
    F[1] = F[0]  # identical twin at another location is allowed, at the same location it is not
    loc[1] = loc[0]
    nb = n1.neighbours_features(F, loc, np.arange(30), np.arange(30))
    for q, rows in nb.items():
        assert all(loc[j] != loc[q] for j, _ in rows)
    assert nb[0][0][0] != 1


def test_feature_ties_order_by_record_index():
    F = np.array([[0.0], [1.0], [-1.0], [5.0]])
    loc = np.array(["a", "b", "c", "d"])
    nb = n1.neighbours_features(F, loc, np.arange(4), [0])
    assert [j for j, _ in nb[0][:2]] == [1, 2]


def test_vote_majority_and_nearest_breaks_ties():
    types = np.array(["x", "y", "y", "x", "z"])
    assert n1.vote([(0, 0.1), (1, 0.2), (2, 0.3), (3, 0.4), (4, 0.5)], types, 1) == "x"
    assert n1.vote([(1, 0.1), (0, 0.2), (2, 0.3), (4, 0.4), (3, 0.5)], types, 5) == "y"
    assert n1.vote([(0, 0.1), (1, 0.2), (2, 0.3), (3, 0.4), (4, 0.5)], types, 5) == "x"


def test_macro_f1_and_auroc_by_hand():
    y = ["a", "a", "b", "b"]
    p = ["a", "b", "b", "b"]
    # a: tp1 fp0 fn1 -> 2/3; b: tp2 fp1 fn0 -> 4/5
    assert n1.macro_f1(y, p) == pytest.approx((2 / 3 + 4 / 5) / 2)
    assert n1.auroc([3, 4], [1, 2]) == 1.0
    assert n1.auroc([1], [1]) == 0.5
    assert n1.f1(y, p, "a") == pytest.approx(2 / 3)


def test_m_neighbours_drop_the_query_location_and_decode_distances():
    w = {"ids": np.array(["1", "2", "3", "4", "5", "6", "7"]),
         "location": np.array(["h1", "h1", "h2", "h2", "h3", "h3", "h4"]),
         "primary": np.array([True] * 7)}
    hexd = lambda d: struct.pack(">d", d).hex()  # noqa: E731
    rows = [["p0001", hexd(0.0)], ["p0002", hexd(0.1)]] + [[f"p000{k}", hexd(0.1 * k)] for k in range(3, 8)]
    m = {"queries": {n1.rid(x): rows for x in w["ids"]}}
    nb = n1.neighbours_m(m, w)
    assert [j for j, _ in nb[0]] == [2, 3, 4, 5, 6]
    assert nb[0][0][1] == 0.30000000000000004


def test_selection_uses_off_on_records_and_locations_per_type():
    e = lambda t, loc, s="off-on": {"appliance": {"type": t, "status": s}, "location": loc}  # noqa: E731
    meta = {"1": e("fan", "h1"), "2": e("fan", "h2"), "3": e("kettle", "lab"), "4": e("fan", "h3", "high"),
            "10": e("lamp", "h1"), "11": e("lamp", "h1", "low")}
    primary, unknown = n1.select(meta)
    assert primary == ["1", "2"] and unknown == ["3", "10"]
