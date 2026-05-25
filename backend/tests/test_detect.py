"""Merge layer: groups raw detections by cell, picks default fix on conflict.

Covers REV-05, REV-06, REV-07, REV-09 from TEST_SCENARIOS.md §B.4.3.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.detect import Detection, detect_all, merge_detections
from app.detectors.base import RawDetection


# ---------------------------------------------------------------------------
# merge_detections: pure grouping/priority logic
# ---------------------------------------------------------------------------


def _df_with_ids():
    return pd.DataFrame({
        "customer": ["Cust_A", "Cust_B", "Cust_C"],
        "product_line": ["P_1", "P_2", "P_3"],
        "2022_1": [100.0, 200.0, 300.0],
    })


def test_empty_raws_yields_empty_list():
    assert merge_detections([], _df_with_ids(), ["customer", "product_line"]) == []


def test_single_raw_passes_through():
    df = _df_with_ids()
    raws = [RawDetection(
        row_idx=0, column="2022_1", value=-100.0,
        detector="negative", suggested_fix="set_to_zero", confidence=1.0,
    )]
    dets = merge_detections(raws, df, ["customer", "product_line"])
    assert len(dets) == 1
    d = dets[0]
    assert d.flagged_by == ["negative"]
    assert d.suggested_fix == "set_to_zero"
    assert d.alternative_fixes == []
    assert d.row_key == {"customer": "Cust_A", "product_line": "P_1"}


# REV-05: negative + refund on the same cell — both want set_to_zero
def test_neg_plus_refund_same_fix_collapses():
    df = _df_with_ids()
    raws = [
        RawDetection(row_idx=1, column="2022_1", value=-1000.0,
                     detector="negative", suggested_fix="set_to_zero", confidence=1.0),
        RawDetection(row_idx=1, column="2022_1", value=-1000.0,
                     detector="refund", suggested_fix="set_to_zero", confidence=0.6),
    ]
    dets = merge_detections(raws, df, ["customer", "product_line"])
    assert len(dets) == 1
    d = dets[0]
    assert set(d.flagged_by) == {"negative", "refund"}
    assert d.suggested_fix == "set_to_zero"
    assert d.alternative_fixes == []  # both agreed
    assert d.confidence == 1.0  # max


# REV-06: negative + outlier on the same cell — both default to set_to_zero
def test_neg_plus_outlier_same_fix_collapses():
    df = _df_with_ids()
    raws = [
        RawDetection(row_idx=0, column="2022_1", value=-50_000.0,
                     detector="negative", suggested_fix="set_to_zero", confidence=1.0),
        RawDetection(row_idx=0, column="2022_1", value=-50_000.0,
                     detector="outlier", suggested_fix="set_to_zero", confidence=1.0),
    ]
    dets = merge_detections(raws, df, ["customer", "product_line"])
    assert len(dets) == 1
    d = dets[0]
    assert set(d.flagged_by) == {"negative", "outlier"}
    assert d.suggested_fix == "set_to_zero"
    assert d.alternative_fixes == []


# REV-07: double-booking alone — split_evenly is the only action
def test_double_booking_alone_has_no_alternatives():
    df = _df_with_ids()
    raws = [RawDetection(
        row_idx=2, column="2022_1", value=5000.0,
        detector="double_booking", suggested_fix="split_evenly", confidence=1.0,
    )]
    dets = merge_detections(raws, df, ["customer", "product_line"])
    assert dets[0].suggested_fix == "split_evenly"
    assert dets[0].alternative_fixes == []


# REV-09 (future): triple overlap (neg + refund + outlier) — all three agree
# on set_to_zero today, so no picker is needed.
def test_triple_overlap_all_agree_on_set_to_zero():
    df = _df_with_ids()
    raws = [
        RawDetection(row_idx=0, column="2022_1", value=-10_000.0,
                     detector="negative", suggested_fix="set_to_zero", confidence=1.0),
        RawDetection(row_idx=0, column="2022_1", value=-10_000.0,
                     detector="refund", suggested_fix="set_to_zero", confidence=1.0),
        RawDetection(row_idx=0, column="2022_1", value=-10_000.0,
                     detector="outlier", suggested_fix="set_to_zero", confidence=1.0),
    ]
    dets = merge_detections(raws, df, ["customer", "product_line"])
    d = dets[0]
    assert set(d.flagged_by) == {"negative", "refund", "outlier"}
    assert d.suggested_fix == "set_to_zero"
    assert d.alternative_fixes == []


# Negative + double-booking: priority pick + split_evenly as the alt
def test_neg_plus_double_booking_collects_all_alternatives():
    df = _df_with_ids()
    raws = [
        RawDetection(row_idx=1, column="2022_1", value=8000.0,
                     detector="negative", suggested_fix="set_to_zero", confidence=1.0),
        RawDetection(row_idx=1, column="2022_1", value=8000.0,
                     detector="double_booking", suggested_fix="split_evenly", confidence=1.0),
    ]
    dets = merge_detections(raws, df, ["customer", "product_line"])
    d = dets[0]
    assert d.suggested_fix == "set_to_zero"
    assert d.alternative_fixes == ["split_evenly"]


def test_distinct_cells_remain_distinct():
    df = _df_with_ids()
    raws = [
        RawDetection(row_idx=0, column="2022_1", value=-1.0,
                     detector="negative", suggested_fix="set_to_zero", confidence=1.0),
        RawDetection(row_idx=1, column="2022_1", value=-2.0,
                     detector="negative", suggested_fix="set_to_zero", confidence=1.0),
    ]
    dets = merge_detections(raws, df, ["customer", "product_line"])
    assert len(dets) == 2
    assert {d.row_idx for d in dets} == {0, 1}


def test_results_sorted_by_row_then_column():
    df = pd.DataFrame({
        "customer": ["A", "B"],
        "product_line": ["X", "Y"],
        "2022_1": [-1.0, -2.0],
        "2022_2": [-3.0, -4.0],
    })
    raws = [
        RawDetection(row_idx=1, column="2022_2", value=-4.0,
                     detector="negative", suggested_fix="set_to_zero", confidence=1.0),
        RawDetection(row_idx=0, column="2022_1", value=-1.0,
                     detector="negative", suggested_fix="set_to_zero", confidence=1.0),
        RawDetection(row_idx=0, column="2022_2", value=-3.0,
                     detector="negative", suggested_fix="set_to_zero", confidence=1.0),
        RawDetection(row_idx=1, column="2022_1", value=-2.0,
                     detector="negative", suggested_fix="set_to_zero", confidence=1.0),
    ]
    dets = merge_detections(raws, df, ["customer", "product_line"])
    keys = [(d.row_idx, d.column) for d in dets]
    assert keys == [(0, "2022_1"), (0, "2022_2"), (1, "2022_1"), (1, "2022_2")]


def test_detection_id_is_unique_per_detection():
    df = _df_with_ids()
    raws = [
        RawDetection(row_idx=i, column="2022_1", value=-1.0,
                     detector="negative", suggested_fix="set_to_zero", confidence=1.0)
        for i in range(3)
    ]
    dets = merge_detections(raws, df, ["customer", "product_line"])
    ids = [d.detection_id for d in dets]
    assert len(set(ids)) == 3


def test_row_key_is_jsonable():
    # numpy int64 / float64 in the dataframe shouldn't bleed into the row_key.
    import numpy as np
    df = pd.DataFrame({
        "customer_id": np.array([1, 2, 3], dtype=np.int64),
        "product_line": ["X", "Y", "Z"],
        "2022_1": [-1.0, -2.0, -3.0],
    })
    raws = [RawDetection(
        row_idx=0, column="2022_1", value=-1.0,
        detector="negative", suggested_fix="set_to_zero", confidence=1.0,
    )]
    dets = merge_detections(raws, df, ["customer_id", "product_line"])
    assert isinstance(dets[0].row_key["customer_id"], int)
    assert dets[0].row_key["customer_id"] == 1


# ---------------------------------------------------------------------------
# detect_all: end-to-end orchestration on a real cube
# ---------------------------------------------------------------------------


def test_detect_all_on_happy_cube():
    df = pd.read_parquet("../data/scenarios/happy.parquet")
    measure = [c for c in df.columns if c not in ("customer", "product_line")]
    dets = detect_all(df, ["customer", "product_line"], measure)
    # The happy.parquet output we sanity-checked earlier:
    # negatives=28, refunds=17, dbl=40, out=161, with overlap producing fewer
    # unique cells than the sum.
    assert len(dets) > 0
    assert all(isinstance(d, Detection) for d in dets)
    # No two detections share a (row, column) — that's the merge invariant.
    keys = [(d.row_idx, d.column) for d in dets]
    assert len(set(keys)) == len(keys)


def test_detect_all_empty_cube_yields_nothing():
    df = pd.DataFrame({"customer": [], "product_line": [], "2022_1": []})
    dets = detect_all(df, ["customer", "product_line"], ["2022_1"])
    assert dets == []
