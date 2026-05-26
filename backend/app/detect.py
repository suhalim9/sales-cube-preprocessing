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
from concurrent.futures import ThreadPoolExecutor
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
    """Run all four detectors concurrently and merge per-cell.

    Each detector is pure numpy + pandas with no shared mutable state, so
    a ThreadPoolExecutor parallelizes them safely. Numpy releases the GIL
    on the heavy ops (percentile, cumsum, comparisons), so wall-clock for
    detection drops to roughly the slowest single detector instead of the
    sum. Outliers is usually the slowest on wide cubes.

    Order of the resulting raw list is preserved (negatives → refunds →
    double_bookings → outliers) so the merge layer's ``flagged_by``
    ordering stays deterministic.
    """
    fns = (
        detect_negatives,
        detect_refunds,
        detect_double_bookings,
        detect_outliers,
    )
    with ThreadPoolExecutor(max_workers=len(fns)) as pool:
        results = list(pool.map(lambda fn: fn(df, measure_columns), fns))
    raws: list[RawDetection] = []
    for r in results:
        raws.extend(r)
    return merge_detections(raws, df, id_columns)


def merge_detections(
    raws: list[RawDetection],
    df: pd.DataFrame,
    id_columns: list[str],
) -> list[Detection]:
    """Group raw detections by ``(row_idx, column)``; deterministic order.

    Hot path on stress.parquet: hundreds of thousands of raw detections
    funnel through here. Three earlier hotspots have been eliminated:

    1. ``df.iloc[row_idx]`` was being called per detection to build the
       row_key. We pre-extract each identifier column once and look up by
       row_idx (cheap list indexing) instead.
    2. ``str(uuid.uuid4())`` ran per detection. Detection IDs only need to
       be unique within a single ``/detect`` invocation, so a counter-based
       scheme (``d_<run_uuid>_<seq>``) is dramatically cheaper than full
       UUID generation, with the same uniqueness guarantee.
    3. ``sorted(grouped.items())`` materialized all tuples at once. We
       still want stable output for tests / golden masters, so we keep the
       sort but on int+str tuples rather than rebuilding the dict.
    """
    grouped: dict[tuple[int, str], list[RawDetection]] = {}
    for r in raws:
        grouped.setdefault((r.row_idx, r.column), []).append(r)

    # Pre-extract identifier columns as flat lists indexed by row position.
    # ``df[col].tolist()`` is O(n) once; per-detection ``df.iloc[row]`` was
    # O(detections) with non-trivial per-call overhead.
    id_values: dict[str, list[Any]] = {
        c: [_jsonable(v) for v in df[c].tolist()] for c in id_columns
    }
    run_id = uuid.uuid4().hex[:8]  # short shared prefix per /detect call

    detections: list[Detection] = []
    for seq, ((row_idx, col), group) in enumerate(sorted(grouped.items())):
        # Detector order preserved for stable flagged_by output.
        flagged_by = _ordered_unique(r.detector for r in group)
        # Default fix is picked from the detectors' *primary* suggestions only.
        # Alternative_fixes are opt-in switches the analyst can flip to from
        # the staged-changes bar — they must not silently overrule another
        # detector's primary suggestion when picking the default. For example,
        # a Double-booking + Outlier overlap should default to ``split_evenly``
        # (DBL's primary) even though Outlier offers ``set_to_zero`` as an
        # alternative.
        primary_fixes = {r.suggested_fix for r in group}
        all_offered: set[SuggestedFix] = set(primary_fixes)
        for r in group:
            all_offered.update(r.alternative_fixes)

        ordered_primary = sorted(primary_fixes, key=lambda f: _FIX_PRIORITY[f])
        suggested = ordered_primary[0]
        alternatives = sorted(all_offered - {suggested}, key=lambda f: _FIX_PRIORITY[f])

        row_key = {c: id_values[c][row_idx] for c in id_columns}

        detections.append(
            Detection(
                detection_id=f"d_{run_id}_{seq}",
                row_idx=row_idx,
                row_key=row_key,
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


def _jsonable(v: Any) -> Any:
    # pandas scalar types (numpy.int64, etc.) need conversion for JSON.
    if hasattr(v, "item"):
        try:
            return v.item()
        except (ValueError, AttributeError):
            pass
    return v
