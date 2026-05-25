"""Double-booking detector.

Pattern: a strictly positive value ``X`` adjacent to a ``0`` in either
neighboring period, where ``X > 2 × row mean of positive cells``. The
zero can be the period before or after — both shapes (``X, 0``) and
(``0, X``) match. Suggested fix is ``split_evenly`` (split the spike
across the spike cell and its zero neighbor).

The detection flags only the spike cell. The apply layer locates the
zero neighbor at apply time and emits the matching audit entries.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import RawDetection


def detect_double_bookings(
    df: pd.DataFrame,
    measure_cols: list[str],
) -> list[RawDetection]:
    # Need at least two columns to have any "neighbor" at all.
    if len(measure_cols) < 2 or df.empty:
        return []

    values = df[measure_cols].to_numpy(dtype=float)

    pos_mask = values > 0
    pos_count = pos_mask.sum(axis=1)
    pos_sum = np.where(pos_mask, values, 0.0).sum(axis=1)
    row_pos_mean = np.where(pos_count > 0, pos_sum / np.maximum(pos_count, 1), 0.0)

    threshold = 2.0 * row_pos_mean[:, None]
    is_spike = (values > 0) & (values > threshold)

    # Per-cell "any neighbor is zero" mask. Each cell's "left neighbor" is
    # the cell to its left in the same row (col i-1); "right neighbor" is
    # col i+1. End cells only have one neighbor.
    has_zero_neighbor = np.zeros_like(is_spike, dtype=bool)
    has_zero_neighbor[:, 1:] |= (values[:, :-1] == 0.0)   # left neighbor is zero
    has_zero_neighbor[:, :-1] |= (values[:, 1:] == 0.0)    # right neighbor is zero

    flagged = is_spike & has_zero_neighbor
    rows, cols = np.where(flagged)
    return [
        RawDetection(
            row_idx=int(r),
            column=measure_cols[int(c)],
            value=float(values[r, c]),
            detector="double_booking",
            suggested_fix="split_evenly",
            confidence=1.0,
        )
        for r, c in zip(rows, cols, strict=True)
    ]
