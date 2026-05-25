"""Negative-value detector.

Flags any measure cell with a value strictly less than zero. Suggested fix
is ``set_to_zero``. Universal catch-all; the refund detector applies its
own pattern logic and frequently re-flags the same cells with a different
attribution.

Notes on edge cases:
- ``-0.0`` does **not** satisfy ``< 0`` in IEEE 754, so it isn't flagged
  (matches TEST_SCENARIOS.md NEG-07).
- ``-1e-9`` is flagged (NEG-08): we deliberately don't impose a
  minimum-magnitude threshold here. The refund detector handles "tiny
  negatives are noise" via its own confidence scoring.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import RawDetection


def detect_negatives(df: pd.DataFrame, measure_cols: list[str]) -> list[RawDetection]:
    if not measure_cols:
        return []

    values = df[measure_cols].to_numpy(dtype=float)
    rows, cols = np.where(values < 0)

    return [
        RawDetection(
            row_idx=int(r),
            column=measure_cols[int(c)],
            value=float(values[r, c]),
            detector="negative",
            suggested_fix="set_to_zero",
            confidence=1.0,
        )
        for r, c in zip(rows, cols, strict=True)
    ]
