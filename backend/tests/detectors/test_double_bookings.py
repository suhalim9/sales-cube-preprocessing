"""TEST_SCENARIOS.md §7 — double-booking detector."""

from __future__ import annotations

import pandas as pd
import pytest

from app.detectors.double_bookings import detect_double_bookings


COLS = ["m1", "m2", "m3", "m4", "m5"]


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# DBL-01
def test_no_double_bookings():
    df = _df([{"id": "a", "m1": 100.0, "m2": 100.0, "m3": 100.0,
               "m4": 100.0, "m5": 100.0}])
    assert detect_double_bookings(df, COLS) == []


# DBL-02
def test_clear_spike_zero_pattern():
    # row mean of positives = (100+100+100+100)/4 = 100; threshold = 200
    df = _df([{"id": "a", "m1": 100.0, "m2": 100.0, "m3": 5_000.0,
               "m4": 0.0, "m5": 100.0}])
    dets = detect_double_bookings(df, COLS)
    assert len(dets) == 1
    assert dets[0].column == "m3"
    assert dets[0].value == 5_000.0
    assert dets[0].suggested_fix == "split_evenly"
    assert dets[0].alternative_fixes == ()


# DBL-03
def test_close_to_mean_spike_not_detected():
    # row mean = 100; threshold = 200; spike = 150 is below threshold.
    df = _df([{"id": "a", "m1": 100.0, "m2": 100.0, "m3": 150.0,
               "m4": 0.0, "m5": 100.0}])
    assert detect_double_bookings(df, COLS) == []


# DBL-04: multiple (X, 0) patterns in same row.
# The row needs enough "normal" cells that two spikes don't pull the
# row-positive-mean above 2× either spike. Wider row solves that.
def test_multiple_patterns_same_row():
    wide_cols = [f"m{i}" for i in range(1, 13)]
    df = _df([{
        "id": "a",
        "m1": 5_000.0, "m2": 0.0, "m3": 5_000.0, "m4": 0.0,
        "m5": 100.0, "m6": 100.0, "m7": 100.0, "m8": 100.0,
        "m9": 100.0, "m10": 100.0, "m11": 100.0, "m12": 100.0,
    }])
    # positives mean = (5000+5000+8·100)/10 = 1080; threshold = 2160; 5000 > 2160 ✓
    dets = detect_double_bookings(df, wide_cols)
    assert {d.column for d in dets} == {"m1", "m3"}


# DBL-05: spike at last column with a zero neighbor on the LEFT → flagged.
# The detector considers either-side neighbors, not just the next column.
def test_spike_at_last_column_with_zero_prev_flagged():
    df = _df([{"id": "a", "m1": 100.0, "m2": 100.0, "m3": 100.0,
               "m4": 0.0, "m5": 5_000.0}])
    dets = detect_double_bookings(df, COLS)
    assert len(dets) == 1
    assert dets[0].column == "m5"


# Companion to DBL-05: spike at last column WITHOUT a zero neighbor → not flagged.
def test_spike_at_last_column_no_zero_neighbor_not_flagged():
    df = _df([{"id": "a", "m1": 100.0, "m2": 100.0, "m3": 100.0,
               "m4": 100.0, "m5": 5_000.0}])
    assert detect_double_bookings(df, COLS) == []


# Bidirectional coverage: spike in a middle column whose ONLY zero
# neighbor is on the left. Was missed by the old next-column-only rule.
def test_spike_with_only_left_neighbor_zero_flagged():
    df = _df([{"id": "a", "m1": 100.0, "m2": 0.0, "m3": 5_000.0,
               "m4": 100.0, "m5": 100.0}])
    dets = detect_double_bookings(df, COLS)
    assert len(dets) == 1
    assert dets[0].column == "m3"


# DBL-06: spike at first column followed by 0
def test_spike_at_first_column():
    df = _df([{"id": "a", "m1": 5_000.0, "m2": 0.0, "m3": 100.0,
               "m4": 100.0, "m5": 100.0}])
    dets = detect_double_bookings(df, COLS)
    assert len(dets) == 1
    assert dets[0].column == "m1"


# DBL-07: (X, 0, 0) → detected on (X, 0); second 0 is unrelated.
def test_spike_followed_by_two_zeros():
    df = _df([{"id": "a", "m1": 100.0, "m2": 100.0, "m3": 5_000.0,
               "m4": 0.0, "m5": 0.0}])
    dets = detect_double_bookings(df, COLS)
    assert len(dets) == 1
    assert dets[0].column == "m3"


# DBL-08: (X, Y) with Y small but nonzero → not detected (neighbor must be exactly 0).
def test_neighbor_must_be_exact_zero():
    df = _df([{"id": "a", "m1": 100.0, "m2": 100.0, "m3": 5_000.0,
               "m4": 0.01, "m5": 100.0}])
    assert detect_double_bookings(df, COLS) == []


# DBL-09: odd amount → detected; split is the apply layer's job.
def test_odd_amount_detected():
    df = _df([{"id": "a", "m1": 30.0, "m2": 30.0, "m3": 101.0,
               "m4": 0.0, "m5": 30.0}])
    # row positive mean = (30+30+101+30)/4 = 47.75; threshold = 95.5; 101 > 95.5.
    dets = detect_double_bookings(df, COLS)
    assert len(dets) == 1
    assert dets[0].value == 101.0


# DBL-10: negative spike → not detected (X > 0 required).
def test_negative_spike_not_detected():
    df = _df([{"id": "a", "m1": 100.0, "m2": 100.0, "m3": -1_000.0,
               "m4": 0.0, "m5": 100.0}])
    assert detect_double_bookings(df, COLS) == []


# DBL-11: all-zero row → no detections.
def test_all_zero_row():
    df = _df([{"id": "a", "m1": 0.0, "m2": 0.0, "m3": 0.0,
               "m4": 0.0, "m5": 0.0}])
    assert detect_double_bookings(df, COLS) == []


# Single-column file → no detections (no neighbor available)
def test_single_column():
    df = _df([{"id": "a", "m1": 5_000.0}])
    assert detect_double_bookings(df, ["m1"]) == []


def test_empty_dataframe():
    df = pd.DataFrame(columns=["id"] + COLS)
    assert detect_double_bookings(df, COLS) == []
