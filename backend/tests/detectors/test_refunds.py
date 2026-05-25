"""TEST_SCENARIOS.md §B.3.2 — refund detector (balance-aware paired reversal)."""

from __future__ import annotations

import pandas as pd

from app.detectors.refunds import detect_refunds


MEASURE_COLS = ["m1", "m2", "m3", "m4", "m5"]


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# REF-01: no negatives → 0 detections
def test_no_negatives_no_detections():
    df = _df([{"id": "a", "m1": 1.0, "m2": 2.0, "m3": 3.0, "m4": 4.0, "m5": 5.0}])
    assert detect_refunds(df, MEASURE_COLS) == []


# REF-02: paired reversal (200 → -200) — prior balance exactly matches
def test_paired_reversal_flagged():
    df = _df([{"id": "a", "m1": 100.0, "m2": 200.0, "m3": -200.0,
               "m4": 100.0, "m5": 50.0}])
    dets = detect_refunds(df, MEASURE_COLS)
    assert len(dets) == 1
    assert dets[0].column == "m3"
    assert dets[0].value == -200.0
    assert dets[0].confidence == 1.0


# REF-03: refund whose magnitude exceeds the row's prior positive balance
def test_refund_exceeding_balance_not_flagged():
    df = _df([{"id": "a", "m1": 0.0, "m2": 550.0, "m3": -8_900.0,
               "m4": 0.0, "m5": 0.0}])
    # Prior balance = 550; |-8,900| > 550 → not flagged.
    assert detect_refunds(df, MEASURE_COLS) == []


# REF-04: negative in first time column — no prior columns, no balance
def test_negative_in_first_column_not_flagged():
    df = _df([{"id": "a", "m1": -200.0, "m2": 0.0, "m3": 0.0,
               "m4": 0.0, "m5": 0.0}])
    assert detect_refunds(df, MEASURE_COLS) == []


# REF-05: prior period is negative — balance from positives further back
def test_negative_with_earlier_positive_balance_flagged():
    df = _df([{"id": "a", "m1": 500.0, "m2": -50.0, "m3": -200.0,
               "m4": 0.0, "m5": 0.0}])
    # m2: prior balance = 500 ≥ 50 → flagged.
    # m3: prior balance = 500 + 0 (m2 negative excluded) = 500 ≥ 200 → flagged.
    dets = detect_refunds(df, MEASURE_COLS)
    cols = {d.column for d in dets}
    assert cols == {"m2", "m3"}


# REF-06: partial refund (small negative after larger positive) — flagged
def test_partial_refund_flagged():
    df = _df([{"id": "a", "m1": 100.0, "m2": 1_000.0, "m3": -50.0,
               "m4": 0.0, "m5": 0.0}])
    dets = detect_refunds(df, MEASURE_COLS)
    assert len(dets) == 1
    assert dets[0].column == "m3"


# REF-07: multiple refunds within the same row, balance allows both
def test_multiple_reversals_in_same_row():
    df = _df([{"id": "a", "m1": 200.0, "m2": -200.0, "m3": 300.0,
               "m4": -300.0, "m5": 100.0}])
    # m2: prior balance = 200 ≥ 200 → flagged.
    # m4: prior balance = 200 - 0 (m2 negative excluded) + 300 = 500
    #     (positives only: 200 + 300 = 500); 500 ≥ 300 → flagged.
    dets = detect_refunds(df, MEASURE_COLS)
    assert len(dets) == 2
    assert {d.column for d in dets} == {"m2", "m4"}


# REF-08: tiny negative after positive — flagged when balance covers it
def test_tiny_negative_after_positive_flagged():
    df = _df([{"id": "a", "m1": 100.0, "m2": 100.0, "m3": -1.0,
               "m4": 100.0, "m5": 100.0}])
    dets = detect_refunds(df, MEASURE_COLS)
    assert len(dets) == 1
    assert dets[0].column == "m3"


# REF-09: refund matches a non-adjacent prior sale — flagged via cumulative balance
def test_refund_against_non_adjacent_prior_sale_flagged():
    df = _df([{"id": "a", "m1": 10_000.0, "m2": 0.0, "m3": -8_900.0,
               "m4": 0.0, "m5": 0.0}])
    # Prior balance at m3 = 10,000 ≥ 8,900 → flagged.
    dets = detect_refunds(df, MEASURE_COLS)
    assert len(dets) == 1
    assert dets[0].column == "m3"


# REF-10: overlap with negatives — both detectors flag the same cell, same fix
def test_overlap_with_negatives_detector():
    df = _df([{"id": "a", "m1": 100.0, "m2": 200.0, "m3": -200.0,
               "m4": 0.0, "m5": 0.0}])
    dets = detect_refunds(df, MEASURE_COLS)
    assert len(dets) == 1
    assert dets[0].detector == "refund"
    assert dets[0].suggested_fix == "set_to_zero"


def test_empty_dataframe():
    df = pd.DataFrame(columns=["id"] + MEASURE_COLS)
    assert detect_refunds(df, MEASURE_COLS) == []


def test_nan_prior_period_not_flagged():
    df = _df([{"id": "a", "m1": float("nan"), "m2": -200.0, "m3": 0.0,
               "m4": 0.0, "m5": 0.0}])
    # NaN does not contribute to the cumulative balance → not flagged.
    assert detect_refunds(df, MEASURE_COLS) == []
