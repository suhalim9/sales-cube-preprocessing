"""Synthetic sales-cube generator.

Produces deterministic ``customer × product × period`` cubes for testing the
cleaning workflow. Output mirrors the shape of real PE revenue-diligence cubes
(see ``data/source/data_cleaning_pvm_cube_input_df_sales.parquet``): two
identifier columns followed by N monthly (or quarterly) measure columns of
non-negative floats. Anomalies of the four supported types are then injected
at random — but seeded — cell positions.

The same ``seed`` + parameters always produces the same DataFrame. Across
anomaly types, no two injections share a cell — this keeps tests' ground
truth clean. (Overlap between detectors is still possible at *detection*
time: an injected refund value is also a negative, and the negatives
detector will pick it up.)
"""

from __future__ import annotations

import calendar
import math
from typing import Literal, TypedDict

import numpy as np
import pandas as pd

TimeFormat = Literal["YYYY_M", "YYYY-MM", "Mon-YY", "YYYYQn"]
Magnitude = Literal["small", "medium", "large"]
RefundStyle = Literal["round", "mom_drop", "mixed"]

# Absolute-value bands for injected negatives. Sign is applied at inject time.
_MAGNITUDE_RANGES: dict[str, tuple[float, float]] = {
    "small": (1.0, 100.0),
    "medium": (100.0, 1_000.0),
    "large": (1_000.0, 50_000.0),
}

_MONTH_ABBR = [calendar.month_abbr[i] for i in range(1, 13)]


class AnomalyConfig(TypedDict, total=False):
    count: int
    magnitude: Magnitude
    style: RefundStyle


def make_cube(
    rows: int = 100,
    time_periods: int = 36,
    seed: int = 42,
    negatives: AnomalyConfig | None = None,
    refunds: AnomalyConfig | None = None,
    double_bookings: AnomalyConfig | None = None,
    outliers: AnomalyConfig | None = None,
    sparsity: float = 0.3,
    noise: float = 0.0,
    time_format: TimeFormat = "YYYY_M",
    start_year: int = 2021,
) -> pd.DataFrame:
    """Build a synthetic sales cube with controlled anomalies.

    Returns a DataFrame with two identifier columns (``customer``,
    ``product_line``) followed by ``time_periods`` measure columns named in
    the requested ``time_format``. Values are non-negative floats around
    row-specific activity levels; ``sparsity`` fraction of cells are zeroed
    before anomaly injection.
    """
    if not 0.0 <= sparsity <= 1.0:
        raise ValueError(f"sparsity must be in [0, 1], got {sparsity}")
    if noise < 0:
        raise ValueError(f"noise must be non-negative, got {noise}")
    if rows < 1 or time_periods < 1:
        raise ValueError("rows and time_periods must be positive")

    rng = np.random.default_rng(seed)

    ids = _build_identifiers(rows, rng)
    time_cols = _build_time_columns(time_periods, time_format, start_year)
    df = _build_base_values(ids, time_cols, sparsity, noise, rng)

    used: set[tuple[int, str]] = set()
    if negatives and negatives.get("count", 0) > 0:
        _inject_negatives(df, time_cols, negatives, rng, used)
    if refunds and refunds.get("count", 0) > 0:
        _inject_refunds(df, time_cols, refunds, rng, used)
    if double_bookings and double_bookings.get("count", 0) > 0:
        _inject_double_bookings(df, time_cols, double_bookings, rng, used)
    if outliers and outliers.get("count", 0) > 0:
        _inject_outliers(df, time_cols, outliers, rng, used)

    return df


# ---------------------------------------------------------------------------
# Identifier and time-column construction
# ---------------------------------------------------------------------------


def _build_identifiers(rows: int, rng: np.random.Generator) -> pd.DataFrame:
    # Pick roughly-square customer × product grid that covers ``rows``; trim
    # the cross-product down to exactly ``rows`` distinct tuples.
    n_customers = max(1, int(math.sqrt(rows * 2)))
    n_products = max(1, math.ceil(rows / n_customers))
    while n_customers * n_products < rows:
        n_products += 1

    customers = [f"Customer_{i + 1}" for i in range(n_customers)]
    products = [f"Product_{chr(ord('A') + i)}" if i < 26 else f"Product_{i + 1}" for i in range(n_products)]

    tuples = [(c, p) for c in customers for p in products][:rows]
    # Deterministic shuffle so the row order doesn't trivially correlate with
    # customer/product, mirroring real cubes.
    rng.shuffle(tuples)
    return pd.DataFrame(tuples, columns=["customer", "product_line"])


def _build_time_columns(periods: int, fmt: TimeFormat, start_year: int) -> list[str]:
    if fmt == "YYYYQn":
        cols = []
        year, q = start_year, 1
        for _ in range(periods):
            cols.append(f"{year}Q{q}")
            q += 1
            if q > 4:
                q = 1
                year += 1
        return cols

    cols = []
    year, month = start_year, 1
    for _ in range(periods):
        if fmt == "YYYY_M":
            cols.append(f"{year}_{month}")
        elif fmt == "YYYY-MM":
            cols.append(f"{year}-{month:02d}")
        elif fmt == "Mon-YY":
            cols.append(f"{_MONTH_ABBR[month - 1]}-{year % 100:02d}")
        else:
            raise ValueError(f"unknown time_format: {fmt}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return cols


# ---------------------------------------------------------------------------
# Base value generation
# ---------------------------------------------------------------------------


def _build_base_values(
    ids: pd.DataFrame,
    time_cols: list[str],
    sparsity: float,
    noise: float,
    rng: np.random.Generator,
) -> pd.DataFrame:
    n_rows = len(ids)
    n_cols = len(time_cols)

    # Per-row activity level (some customer×product pairs are bigger than
    # others). Log-normal gives the long-tailed distribution real cubes have.
    row_scale = rng.lognormal(mean=0.0, sigma=0.6, size=n_rows)

    base = rng.lognormal(mean=7.0, sigma=0.7, size=(n_rows, n_cols))
    values = base * row_scale[:, None]

    if noise > 0:
        values = values * (1.0 + noise * rng.standard_normal(values.shape))
        values = np.clip(values, 0.0, None)

    if sparsity > 0:
        zero_mask = rng.random(values.shape) < sparsity
        values[zero_mask] = 0.0

    measure = pd.DataFrame(values, columns=time_cols)
    return pd.concat([ids.reset_index(drop=True), measure], axis=1)


# ---------------------------------------------------------------------------
# Anomaly injection
# ---------------------------------------------------------------------------


def _pick_positions(
    n_rows: int,
    cols: list[str],
    count: int,
    rng: np.random.Generator,
    used: set[tuple[int, str]],
    *,
    exclude_last_col: bool = False,
    exclude_extra: set[tuple[int, str]] | None = None,
) -> list[tuple[int, str]]:
    """Pick ``count`` cell positions that don't collide with anything used.

    When the caller needs the next column too (double-bookings), it can pass
    ``exclude_last_col=True`` and use ``exclude_extra`` to reserve the
    neighbor cells.
    """
    pool_cols = cols[:-1] if exclude_last_col else cols
    total = n_rows * len(pool_cols)
    if count > total:
        raise ValueError(f"requested {count} positions but only {total} cells available")

    picked: list[tuple[int, str]] = []
    attempts = 0
    # Worst case ~ count^2 random picks; cap so a saturated cube fails loudly.
    max_attempts = max(count * 20, 1000)
    extra = exclude_extra or set()
    while len(picked) < count and attempts < max_attempts:
        row_idx = int(rng.integers(0, n_rows))
        col = pool_cols[int(rng.integers(0, len(pool_cols)))]
        pos = (row_idx, col)
        if pos in used or pos in extra:
            attempts += 1
            continue
        picked.append(pos)
        used.add(pos)
        attempts += 1
    if len(picked) < count:
        raise RuntimeError(
            f"could not pick {count} non-overlapping positions after {max_attempts} attempts"
        )
    return picked


def _inject_negatives(
    df: pd.DataFrame,
    cols: list[str],
    config: AnomalyConfig,
    rng: np.random.Generator,
    used: set[tuple[int, str]],
) -> None:
    count = config["count"]
    lo, hi = _MAGNITUDE_RANGES[config.get("magnitude", "medium")]
    for row_idx, col in _pick_positions(len(df), cols, count, rng, used):
        df.at[row_idx, col] = -float(rng.uniform(lo, hi))


def _inject_refunds(
    df: pd.DataFrame,
    cols: list[str],
    config: AnomalyConfig,
    rng: np.random.Generator,
    used: set[tuple[int, str]],
) -> None:
    count = config["count"]
    style = config.get("style", "mixed")

    if style == "round":
        styles = ["round"] * count
    elif style == "mom_drop":
        styles = ["mom_drop"] * count
    else:
        # Alternate: roughly half round, half mom_drop.
        styles = ["round" if i % 2 == 0 else "mom_drop" for i in range(count)]

    positions = _pick_positions(len(df), cols, count, rng, used)
    for (row_idx, col), s in zip(positions, styles, strict=True):
        if s == "round":
            # Multiple of 100, ≥ 1000 → triggers round_reversal signal.
            k = int(rng.integers(10, 200))  # 1000 .. 19900
            df.at[row_idx, col] = -float(k * 100)
        else:
            # Negative value sized to row's positive mean, ≥ 50% of it.
            row_vals = df.iloc[row_idx][cols].to_numpy(dtype=float)
            positives = row_vals[row_vals > 0]
            base = float(positives.mean()) if positives.size else 5000.0
            magnitude = max(120.0, base * float(rng.uniform(0.6, 1.2)))
            df.at[row_idx, col] = -magnitude


def _inject_double_bookings(
    df: pd.DataFrame,
    cols: list[str],
    config: AnomalyConfig,
    rng: np.random.Generator,
    used: set[tuple[int, str]],
) -> None:
    count = config["count"]
    # Reserve the neighbor cells too so a later injection can't overwrite
    # the trailing zero or the spike's partner.
    neighbor_reserved: set[tuple[int, str]] = set()

    positions = _pick_positions(
        len(df),
        cols,
        count,
        rng,
        used,
        exclude_last_col=True,
        exclude_extra=neighbor_reserved,
    )
    for row_idx, col in positions:
        col_idx = cols.index(col)
        next_col = cols[col_idx + 1]
        # If next cell is already reserved by another anomaly, skip the
        # injection — keeps invariants tight at the cost of dropping a few.
        if (row_idx, next_col) in used:
            continue

        row_vals = df.iloc[row_idx][cols].to_numpy(dtype=float)
        positives = row_vals[row_vals > 0]
        row_mean = float(positives.mean()) if positives.size else 1000.0
        spike = max(5000.0, row_mean * 4.0)
        df.at[row_idx, col] = float(spike)
        df.at[row_idx, next_col] = 0.0
        used.add((row_idx, next_col))


def _inject_outliers(
    df: pd.DataFrame,
    cols: list[str],
    config: AnomalyConfig,
    rng: np.random.Generator,
    used: set[tuple[int, str]],
) -> None:
    count = config["count"]
    for row_idx, col in _pick_positions(len(df), cols, count, rng, used):
        row_vals = df.iloc[row_idx][cols].to_numpy(dtype=float)
        row_max = float(np.nanmax(row_vals)) if row_vals.size else 1000.0
        # Sit well outside any plausible Q3 + 1.5·IQR band.
        spike = max(row_max * 10.0, 100_000.0)
        df.at[row_idx, col] = spike
