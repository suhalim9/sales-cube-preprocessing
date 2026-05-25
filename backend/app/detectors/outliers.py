"""Outlier detector — IQR per row.

For each row, compute Q1, Q3, and IQR over the measure columns. Flag any
cell outside ``[Q1 − 1.5·IQR, Q3 + 1.5·IQR]``. Default action is
``keep_as_is`` — outliers can be legitimate spikes (a year-end customer
order, an off-cycle settlement), so auto-changing the value would be
dangerous. The analyst can switch to ``set_to_zero`` from the change log
if they decide the cell is actually a data error.

Notes on edge cases:
- IQR = 0 rows (all-zero, all-identical, mostly-one-value) collapse the
  bounds to a single point; cells off that point are flagged.
- A row with one measure column can't compute quartiles, so the detector
  returns nothing rather than guessing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import RawDetection


def detect_outliers(df: pd.DataFrame, measure_cols: list[str]) -> list[RawDetection]:
    # OUT-06: degenerate single-column cube — quartiles aren't defined.
    if len(measure_cols) < 2 or df.empty:
        return []

    values = df[measure_cols].to_numpy(dtype=float)
    q1 = np.percentile(values, 25, axis=1, method="linear")
    q3 = np.percentile(values, 75, axis=1, method="linear")
    iqr = q3 - q1

    lower = (q1 - 1.5 * iqr)[:, None]
    upper = (q3 + 1.5 * iqr)[:, None]

    flagged = (values < lower) | (values > upper)
    rows, cols = np.where(flagged)

    return [
        RawDetection(
            row_idx=int(r),
            column=measure_cols[int(c)],
            value=float(values[r, c]),
            detector="outlier",
            suggested_fix="keep_as_is",
            confidence=1.0,
            alternative_fixes=("set_to_zero",),
        )
        for r, c in zip(rows, cols, strict=True)
    ]
