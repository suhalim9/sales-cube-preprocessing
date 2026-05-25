"""Refund detector — balance-aware paired reversal.

A refund is a negative cell where the cumulative positive activity in the
same row, in earlier periods, is large enough to absorb the reversal. The
detector only surfaces refunds the cleaning fix can actually unwind — a
`-8,900` against `[550]` of prior sales is not surfaced as a refund (the
negatives detector still catches it).

Suggested fix: ``set_to_zero``. At apply time the refund cell is zeroed and
the reversal is matched against prior positive periods, walking backward
until the magnitude is exhausted (see :mod:`app.apply`).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import RawDetection


def detect_refunds(df: pd.DataFrame, measure_cols: list[str]) -> list[RawDetection]:
    if not measure_cols or df.empty:
        return []

    values = df[measure_cols].to_numpy(dtype=float)

    # Cumulative sum of strictly-positive cells along each row, then shift one
    # column to the right so column ``c`` reports the balance accumulated by
    # the time we reach (but before) column ``c``.
    positives = np.where(values > 0, values, 0.0)
    cum_pos = np.cumsum(positives, axis=1)
    prior_balance = np.zeros_like(values)
    prior_balance[:, 1:] = cum_pos[:, :-1]

    flagged = (values < 0) & (prior_balance >= np.abs(values))

    rows, cols = np.where(flagged)
    return [
        RawDetection(
            row_idx=int(r),
            column=measure_cols[int(c)],
            value=float(values[r, c]),
            detector="refund",
            suggested_fix="set_to_zero",
            confidence=1.0,
        )
        for r, c in zip(rows, cols, strict=True)
    ]
