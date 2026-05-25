"""Detection orchestrator and merge layer.

Runs the four detectors and groups their raw outputs by ``(row_idx, column)``
into unified ``Detection`` objects with ``flagged_by`` arrays. This is what
the API returns — see ``DATA_MODEL.md``'s "Detection contract".

Conflict handling: when multiple detectors flag the same cell with
different fixes, ``suggested_fix`` defaults to the highest-priority fix
(``set_to_zero`` wins). All other options stay in ``alternative_fixes``
for the UI to surface as a picker (REV-06). When all contributing
detectors agree on the same fix, ``alternative_fixes`` is empty and the
UI shows a single checkbox (REV-05).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .detectors.base import AnomalyType, RawDetection, SuggestedFix
from .detectors.double_bookings import detect_double_bookings
from .detectors.negatives import detect_negatives
from .detectors.outliers import detect_outliers
from .detectors.refunds import detect_refunds

# Priority for picking the default fix on conflict. Lower index = higher
# priority. ``set_to_zero`` wins because it's the most aggressive cleanup;
# ``keep_as_is`` loses because it doesn't change anything.
_FIX_PRIORITY: dict[SuggestedFix, int] = {
    "set_to_zero": 0,
    "split_evenly": 1,
    "keep_as_is": 2,
}


@dataclass
class Detection:
    detection_id: str
    row_idx: int
    row_key: dict[str, Any]
    column: str
    value: float
    flagged_by: list[AnomalyType]
    suggested_fix: SuggestedFix
    confidence: float
    alternative_fixes: list[SuggestedFix] = field(default_factory=list)


def detect_all(
    df: pd.DataFrame,
    id_columns: list[str],
    measure_columns: list[str],
) -> list[Detection]:
    """Run all four detectors and merge per-cell."""
    raws: list[RawDetection] = []
    raws.extend(detect_negatives(df, measure_columns))
    raws.extend(detect_refunds(df, measure_columns))
    raws.extend(detect_double_bookings(df, measure_columns))
    raws.extend(detect_outliers(df, measure_columns))
    return merge_detections(raws, df, id_columns)


def merge_detections(
    raws: list[RawDetection],
    df: pd.DataFrame,
    id_columns: list[str],
) -> list[Detection]:
    """Group raw detections by ``(row_idx, column)``; deterministic order."""
    grouped: dict[tuple[int, str], list[RawDetection]] = {}
    for r in raws:
        grouped.setdefault((r.row_idx, r.column), []).append(r)

    detections: list[Detection] = []
    for (row_idx, col), group in sorted(grouped.items()):
        # Detector order preserved for stable flagged_by output.
        flagged_by = _ordered_unique(r.detector for r in group)
        contributing_fixes: set[SuggestedFix] = set()
        for r in group:
            contributing_fixes.add(r.suggested_fix)
            contributing_fixes.update(r.alternative_fixes)

        ordered_fixes = sorted(contributing_fixes, key=lambda f: _FIX_PRIORITY[f])
        suggested = ordered_fixes[0]
        alternatives = ordered_fixes[1:]

        detections.append(
            Detection(
                detection_id=str(uuid.uuid4()),
                row_idx=row_idx,
                row_key=_row_key(df, row_idx, id_columns),
                column=col,
                value=group[0].value,
                flagged_by=flagged_by,
                suggested_fix=suggested,
                confidence=max(r.confidence for r in group),
                alternative_fixes=alternatives,
            )
        )
    return detections


def _ordered_unique(items) -> list:
    seen: set = set()
    out: list = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _row_key(df: pd.DataFrame, row_idx: int, id_columns: list[str]) -> dict[str, Any]:
    row = df.iloc[row_idx]
    return {c: _jsonable(row[c]) for c in id_columns}


def _jsonable(v: Any) -> Any:
    # pandas scalar types (numpy.int64, etc.) need conversion for JSON.
    if hasattr(v, "item"):
        try:
            return v.item()
        except (ValueError, AttributeError):
            pass
    return v
