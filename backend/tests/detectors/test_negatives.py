"""TEST_SCENARIOS.md §5 — negatives detector."""

from __future__ import annotations

import pandas as pd
import pytest

from app.detectors.negatives import detect_negatives
from data.generator import make_cube


MEASURE_COLS = ["m1", "m2", "m3"]


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# NEG-01
def test_no_negatives_no_detections():
    df = _df([{"id": "a", "m1": 1.0, "m2": 2.0, "m3": 3.0}])
    assert detect_negatives(df, MEASURE_COLS) == []


# NEG-02
def test_one_negative_one_detection():
    df = _df([{"id": "a", "m1": 1.0, "m2": -5.0, "m3": 3.0}])
    dets = detect_negatives(df, MEASURE_COLS)
    assert len(dets) == 1
    d = dets[0]
    assert d.row_idx == 0
    assert d.column == "m2"
    assert d.value == -5.0
    assert d.detector == "negative"
    assert d.suggested_fix == "set_to_zero"


# NEG-03
def test_many_negatives_correct_count():
    df = make_cube(
        rows=500, time_periods=24, seed=1, sparsity=0.0,
        negatives={"count": 1000, "magnitude": "medium"},
    )
    measure_cols = [c for c in df.columns if c not in ("customer", "product_line")]
    dets = detect_negatives(df, measure_cols)
    assert len(dets) == 1000


# NEG-04
def test_all_measure_negative():
    df = _df([{"id": "a", "m1": -1.0, "m2": -2.0, "m3": -3.0}])
    dets = detect_negatives(df, MEASURE_COLS)
    assert len(dets) == 3


# NEG-05: negatives in non-measure columns are ignored (we only iterate measure_cols).
def test_negative_in_identifier_column_ignored():
    df = _df([{"id_neg": -42, "m1": 1.0, "m2": 2.0, "m3": 3.0}])
    assert detect_negatives(df, MEASURE_COLS) == []


# NEG-06
def test_zero_not_flagged():
    df = _df([{"id": "a", "m1": 0.0, "m2": 0.0, "m3": 0.0}])
    assert detect_negatives(df, MEASURE_COLS) == []


# NEG-07
def test_negative_zero_not_flagged():
    df = _df([{"id": "a", "m1": -0.0, "m2": 0.0, "m3": 1.0}])
    assert detect_negatives(df, MEASURE_COLS) == []


# NEG-08
def test_tiny_negative_flagged():
    df = _df([{"id": "a", "m1": -0.000001, "m2": 0.0, "m3": 1.0}])
    dets = detect_negatives(df, MEASURE_COLS)
    assert len(dets) == 1
    assert dets[0].column == "m1"


# Empty input
def test_empty_measure_cols():
    df = _df([{"id": "a", "m1": -1.0}])
    assert detect_negatives(df, []) == []


def test_empty_dataframe():
    df = pd.DataFrame(columns=["id", "m1", "m2", "m3"])
    assert detect_negatives(df, MEASURE_COLS) == []


# Multi-row, scattered negatives
def test_multi_row_scattered():
    df = _df([
        {"id": "a", "m1": 1.0, "m2": -5.0, "m3": 3.0},
        {"id": "b", "m1": -1.0, "m2": 2.0, "m3": -3.0},
        {"id": "c", "m1": 1.0, "m2": 2.0, "m3": 3.0},
    ])
    dets = detect_negatives(df, MEASURE_COLS)
    found = {(d.row_idx, d.column, d.value) for d in dets}
    assert found == {(0, "m2", -5.0), (1, "m1", -1.0), (1, "m3", -3.0)}


# Confidence is always 1.0 for negatives (boolean detector)
def test_confidence_always_one():
    df = _df([{"id": "a", "m1": -1.0, "m2": -2.0, "m3": 3.0}])
    dets = detect_negatives(df, MEASURE_COLS)
    assert all(d.confidence == 1.0 for d in dets)
