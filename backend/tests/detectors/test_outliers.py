"""TEST_SCENARIOS.md §8 — outlier detector (IQR per row)."""

from __future__ import annotations

import pandas as pd
import pytest

from app.detectors.outliers import detect_outliers


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# OUT-01: all identical → IQR = 0, no values outside the single-point band
def test_all_identical_non_zero_no_outliers():
    df = _df([{"id": "a", "m1": 100.0, "m2": 100.0, "m3": 100.0, "m4": 100.0,
               "m5": 100.0}])
    assert detect_outliers(df, ["m1", "m2", "m3", "m4", "m5"]) == []


# OUT-02: one extreme positive spike vs uniform background
def test_single_positive_spike_flagged():
    df = _df([{"id": "a",
               "m1": 100.0, "m2": 100.0, "m3": 100.0, "m4": 100.0,
               "m5": 100.0, "m6": 100.0, "m7": 100.0, "m8": 100.0,
               "m9": 100.0, "m10": 100_000.0}])
    cols = [f"m{i}" for i in range(1, 11)]
    dets = detect_outliers(df, cols)
    assert len(dets) == 1
    assert dets[0].column == "m10"
    assert dets[0].suggested_fix == "keep_as_is"


# OUT-03: two extreme spikes — both flagged
def test_two_extreme_spikes_both_flagged():
    df = _df([{"id": "a",
               "m1": 100.0, "m2": 100.0, "m3": 100.0, "m4": 100.0,
               "m5": 100.0, "m6": 100.0, "m7": 100.0, "m8": 100.0,
               "m9": 100_000.0, "m10": 100_000.0}])
    cols = [f"m{i}" for i in range(1, 11)]
    dets = detect_outliers(df, cols)
    assert {d.column for d in dets} == {"m9", "m10"}


# OUT-04: all zeros → IQR = 0, no outliers
def test_all_zero_row():
    df = _df([{"id": "a", "m1": 0.0, "m2": 0.0, "m3": 0.0, "m4": 0.0, "m5": 0.0}])
    assert detect_outliers(df, ["m1", "m2", "m3", "m4", "m5"]) == []


# OUT-05: all-negative row — IQR is well-defined; outliers possible
def test_all_negative_row_can_have_outliers():
    df = _df([{"id": "a",
               "m1": -100.0, "m2": -100.0, "m3": -100.0, "m4": -100.0,
               "m5": -100.0, "m6": -100.0, "m7": -100.0, "m8": -100.0,
               "m9": -100.0, "m10": -100_000.0}])
    cols = [f"m{i}" for i in range(1, 11)]
    dets = detect_outliers(df, cols)
    assert len(dets) == 1
    assert dets[0].column == "m10"


# OUT-06: degenerate single-column cube → no detections
def test_single_column_no_detections():
    df = _df([{"id": "a", "m1": 100.0}])
    assert detect_outliers(df, ["m1"]) == []


# OUT-07: [0,0,0,0,100] → 100 is flagged above the IQR=0 band
def test_iqr_zero_above_band():
    df = _df([{"id": "a", "m1": 0.0, "m2": 0.0, "m3": 0.0, "m4": 0.0, "m5": 100.0}])
    dets = detect_outliers(df, ["m1", "m2", "m3", "m4", "m5"])
    assert len(dets) == 1
    assert dets[0].column == "m5"
    assert dets[0].value == 100.0


# OUT-08: [1000, 1000, ..., 0] → the lone 0 is flagged below the band
def test_iqr_zero_below_band():
    df = _df([{"id": "a",
               "m1": 1000.0, "m2": 1000.0, "m3": 1000.0, "m4": 1000.0,
               "m5": 1000.0, "m6": 1000.0, "m7": 1000.0, "m8": 1000.0,
               "m9": 1000.0, "m10": 0.0}])
    cols = [f"m{i}" for i in range(1, 11)]
    dets = detect_outliers(df, cols)
    assert len(dets) == 1
    assert dets[0].column == "m10"
    assert dets[0].value == 0.0


# Multi-row sanity
def test_multi_row_per_row_independent():
    df = _df([
        {"id": "a", "m1": 100.0, "m2": 100.0, "m3": 100.0, "m4": 100.0,
         "m5": 100.0, "m6": 100.0, "m7": 100.0, "m8": 100.0,
         "m9": 100.0, "m10": 100_000.0},
        {"id": "b", "m1": 50.0, "m2": 50.0, "m3": 50.0, "m4": 50.0,
         "m5": 50.0, "m6": 50.0, "m7": 50.0, "m8": 50.0,
         "m9": 50.0, "m10": 50.0},
    ])
    cols = [f"m{i}" for i in range(1, 11)]
    dets = detect_outliers(df, cols)
    assert len(dets) == 1
    assert dets[0].row_idx == 0


def test_empty_dataframe():
    df = pd.DataFrame(columns=["id", "m1", "m2"])
    assert detect_outliers(df, ["m1", "m2"]) == []


# Confidence is fixed at 1.0 (outliers are boolean per the band check)
def test_confidence_always_one():
    df = _df([{"id": "a", "m1": 0.0, "m2": 0.0, "m3": 100.0,
               "m4": 0.0, "m5": 0.0}])
    dets = detect_outliers(df, ["m1", "m2", "m3", "m4", "m5"])
    assert all(d.confidence == 1.0 for d in dets)
