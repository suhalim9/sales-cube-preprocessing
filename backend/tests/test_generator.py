"""Tests for the synthetic data generator.

The generator is the foundation for every detection test downstream, so we
verify here that:
- it's deterministic given a seed,
- shape and column layout match expectations across all 4 time formats,
- each anomaly type injects ~the requested count of cells matching that
  detector's pattern (not necessarily exact, since injections may
  intersect detector criteria belonging to other types).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.generator import _build_time_columns, make_cube


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_same_seed_same_output():
    a = make_cube(rows=50, time_periods=12, seed=7)
    b = make_cube(rows=50, time_periods=12, seed=7)
    pd.testing.assert_frame_equal(a, b)


def test_different_seed_different_output():
    a = make_cube(rows=50, time_periods=12, seed=1)
    b = make_cube(rows=50, time_periods=12, seed=2)
    assert not a.equals(b)


# ---------------------------------------------------------------------------
# Shape & schema
# ---------------------------------------------------------------------------


def test_shape_matches_request():
    df = make_cube(rows=87, time_periods=18)
    assert df.shape == (87, 18 + 2)
    assert list(df.columns[:2]) == ["customer", "product_line"]


def test_identifier_tuples_unique():
    df = make_cube(rows=200, time_periods=12)
    tuples = list(zip(df["customer"], df["product_line"], strict=True))
    assert len(set(tuples)) == len(tuples)


def test_measure_columns_are_numeric():
    df = make_cube(rows=20, time_periods=6)
    measure = df.drop(columns=["customer", "product_line"])
    assert all(pd.api.types.is_numeric_dtype(measure[c]) for c in measure.columns)


@pytest.mark.parametrize(
    "fmt, expected",
    [
        ("YYYY_M", ["2021_1", "2021_2", "2021_3"]),
        ("YYYY-MM", ["2021-01", "2021-02", "2021-03"]),
        ("Mon-YY", ["Jan-21", "Feb-21", "Mar-21"]),
        ("YYYYQn", ["2021Q1", "2021Q2", "2021Q3"]),
    ],
)
def test_time_format_first_three(fmt, expected):
    cols = _build_time_columns(3, fmt, start_year=2021)
    assert cols == expected


def test_time_columns_roll_over_year():
    cols = _build_time_columns(14, "YYYY_M", start_year=2021)
    assert cols[-2:] == ["2022_1", "2022_2"]


def test_quarterly_rolls_over_year():
    cols = _build_time_columns(6, "YYYYQn", start_year=2021)
    assert cols == ["2021Q1", "2021Q2", "2021Q3", "2021Q4", "2022Q1", "2022Q2"]


# ---------------------------------------------------------------------------
# Clean-cube properties (no anomalies requested)
# ---------------------------------------------------------------------------


def test_clean_cube_has_no_negatives():
    df = make_cube(rows=200, time_periods=24, seed=3)
    measure = df.drop(columns=["customer", "product_line"]).to_numpy()
    assert (measure >= 0).all()


def test_sparsity_creates_zeros():
    df = make_cube(rows=300, time_periods=24, seed=5, sparsity=0.5)
    measure = df.drop(columns=["customer", "product_line"]).to_numpy()
    zero_fraction = (measure == 0).mean()
    assert 0.4 <= zero_fraction <= 0.6


def test_zero_sparsity_means_no_zeros():
    df = make_cube(rows=100, time_periods=12, seed=11, sparsity=0.0)
    measure = df.drop(columns=["customer", "product_line"]).to_numpy()
    assert (measure > 0).all()


# ---------------------------------------------------------------------------
# Anomaly injection — count and shape
# ---------------------------------------------------------------------------


def _measure(df: pd.DataFrame) -> pd.DataFrame:
    return df.drop(columns=["customer", "product_line"])


def test_negatives_injection_matches_count():
    df = make_cube(rows=100, time_periods=24, seed=42, sparsity=0.0,
                   negatives={"count": 30, "magnitude": "medium"})
    measure = _measure(df).to_numpy()
    assert (measure < 0).sum() == 30


def test_negatives_magnitude_band():
    df = make_cube(rows=200, time_periods=24, seed=42,
                   negatives={"count": 100, "magnitude": "large"})
    measure = _measure(df).to_numpy()
    negs = measure[measure < 0]
    assert (np.abs(negs) >= 1000).all() and (np.abs(negs) <= 50_000).all()


def test_refunds_round_style_are_multiples_of_100():
    df = make_cube(rows=100, time_periods=24, seed=42,
                   refunds={"count": 40, "style": "round"})
    measure = _measure(df).to_numpy()
    negs = np.abs(measure[measure < 0])
    # All injected negatives should be round multiples of 100 with abs ≥ 1000.
    assert (negs % 100 == 0).all()
    assert (negs >= 1000).all()


def test_double_bookings_create_spike_zero_pairs():
    df = make_cube(rows=100, time_periods=24, seed=42, sparsity=0.0,
                   double_bookings={"count": 10})
    measure = _measure(df)
    pairs_found = 0
    cols = list(measure.columns)
    for i in range(len(cols) - 1):
        for r in range(len(measure)):
            a = measure.iat[r, i]
            b = measure.iat[r, i + 1]
            if a > 0 and b == 0:
                row_vals = measure.iloc[r].to_numpy()
                row_mean_pos = row_vals[row_vals > 0].mean()
                if a > 2 * row_mean_pos:
                    pairs_found += 1
    # Generator might skip some if neighbor already reserved; expect at least
    # the count we asked for or close to it.
    assert pairs_found >= 8


def test_outliers_create_extreme_high_values():
    df = make_cube(rows=100, time_periods=24, seed=42,
                   outliers={"count": 10})
    measure = _measure(df).to_numpy()
    # Outliers are injected at >= 100k or 10x row max; whichever larger.
    big = (measure >= 100_000).sum()
    assert big >= 10


def test_combined_injections_no_cell_overlap():
    # All four anomaly types together — should still be deterministic and
    # produce a valid frame.
    df = make_cube(
        rows=200, time_periods=24, seed=42, sparsity=0.3,
        negatives={"count": 40, "magnitude": "medium"},
        refunds={"count": 15, "style": "mixed"},
        double_bookings={"count": 8},
        outliers={"count": 12},
    )
    assert df.shape == (200, 24 + 2)
    measure = _measure(df).to_numpy()
    # Sanity: negatives & refunds together = 55 negative cells (refunds also
    # inject negative values, but never on top of negatives' positions).
    assert (measure < 0).sum() == 55


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_invalid_sparsity_raises():
    with pytest.raises(ValueError):
        make_cube(rows=10, sparsity=1.5)


def test_invalid_noise_raises():
    with pytest.raises(ValueError):
        make_cube(rows=10, noise=-1.0)


def test_zero_rows_raises():
    with pytest.raises(ValueError):
        make_cube(rows=0)


def test_request_too_many_negatives_raises():
    # 10 rows × 5 cols = 50 cells; request 100 negatives.
    with pytest.raises(ValueError):
        make_cube(rows=10, time_periods=5, negatives={"count": 100})
