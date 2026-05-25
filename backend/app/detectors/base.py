"""Shared types for detectors.

A ``RawDetection`` is the per-detector output — one cell, one detector. The
merging layer (``app.detectors.merge``) later groups raw detections by
``(row_idx, column)`` to produce the unified API detections with
``flagged_by`` arrays.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AnomalyType = Literal["negative", "refund", "double_booking", "outlier"]
SuggestedFix = Literal["set_to_zero", "split_evenly", "keep_as_is"]


@dataclass(frozen=True)
class RawDetection:
    row_idx: int
    column: str
    value: float
    detector: AnomalyType
    suggested_fix: SuggestedFix
    confidence: float
    alternative_fixes: tuple[SuggestedFix, ...] = field(default_factory=tuple)
