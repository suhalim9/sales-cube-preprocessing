"""Apply layer — produce a cleaned cube and an audit log from user selections.

Given a list of ``Detection`` objects and the user's chosen fixes, this
module:

1. Validates each selection against the detection's allowed fixes.
2. Mutates a copy of the input DataFrame.
3. Emits audit entries — one per cell change, two for ``split_evenly``
   double-bookings (the spike cell **and** the zero neighbor).
4. Builds the audit summary header matching ``DATA_MODEL.md`` §"Audit log".

The function is pure: same inputs → same outputs (timestamps and UUIDs
aside; both can be injected for testing). No I/O — the storage layer is
responsible for writing the cleaned Parquet and audit JSON to S3.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from .detect import Detection
from .detectors.base import AnomalyType, SuggestedFix

# Which detectors suggest each fix. Used to attribute an audit change to
# exactly one detector when multiple flagged the same cell.
_FIX_DETECTORS: dict[SuggestedFix, list[AnomalyType]] = {
    # Priority order matters: first match wins. Refund is more specific than
    # negative (refund cells are a subset of negative cells), so a cell flagged
    # by both is treated as a refund. Outliers fall through to the last slot
    # in set_to_zero — they're the catch-all and lose to any other detector.
    "set_to_zero": ["refund", "negative", "outlier"],
    "split_evenly": ["double_booking"],
    # ``keep_as_is`` is a generic "I looked at this and chose not to change it"
    # action. Today only outliers default to it; any detector could offer it.
    "keep_as_is": ["outlier", "negative", "refund", "double_booking"],
}


class InvalidSelectionError(ValueError):
    """A selection references a fix the detection doesn't allow."""


@dataclass
class Selection:
    detection_id: str
    fix: SuggestedFix
    # Which detector the user staged the fix from. Captures the active left-
    # rail tab in the UI: a cell flagged by both negative and refund can be
    # staged as either, depending on what the user was looking at. When None
    # (e.g., staged from the "All" view), apply falls back to the priority
    # order in ``_FIX_DETECTORS``.
    attribution: AnomalyType | None = None


@dataclass
class ApplyResult:
    cleaned_df: pd.DataFrame
    audit: dict[str, Any] = field(default_factory=dict)


def apply_selections(
    df: pd.DataFrame,
    detections: list[Detection],
    selections: list[Selection],
    *,
    file_id: str,
    measure_columns: list[str],
    project_slug: str = "demo",
    applied_at: datetime | None = None,
) -> ApplyResult:
    """Apply selected fixes and return cleaned DataFrame + audit dict."""
    applied_at = applied_at or datetime.now(UTC)
    by_id: dict[str, Detection] = {d.detection_id: d for d in detections}

    cleaned = df.copy(deep=True)
    changes: list[dict[str, Any]] = []
    summary: dict[str, int] = {
        "negative": 0,
        "refund": 0,
        "double_booking": 0,
        "outlier": 0,
    }

    for sel in selections:
        det = by_id.get(sel.detection_id)
        if det is None:
            raise InvalidSelectionError(f"Unknown detection_id: {sel.detection_id}")

        allowed = {det.suggested_fix, *det.alternative_fixes}
        if sel.fix not in allowed:
            raise InvalidSelectionError(
                f"Fix '{sel.fix}' not allowed for detection {sel.detection_id} "
                f"(allowed: {sorted(allowed)})"
            )

        attribution = _attribute(det, sel.fix, sel.attribution)
        new_changes = _apply_one(cleaned, df, det, sel.fix, measure_columns, attribution)
        changes.extend(new_changes)
        summary[attribution] += len(new_changes)

    audit = {
        "file_id": file_id,
        "applied_at": _iso_utc(applied_at),
        "user_id": f"project:{project_slug}",
        "summary": summary,
        "changes": changes,
    }
    return ApplyResult(cleaned_df=cleaned, audit=audit)


# ---------------------------------------------------------------------------
# Per-fix application
# ---------------------------------------------------------------------------


def _apply_one(
    df: pd.DataFrame,
    original: pd.DataFrame,
    det: Detection,
    fix: SuggestedFix,
    measure_columns: list[str],
    attribution: AnomalyType,
) -> list[dict[str, Any]]:
    """Mutate ``df`` (cleaned copy) for one selection; return audit entries.

    ``original`` is the unmutated source DataFrame — used to locate a
    detection's zero neighbor as it was *at detection time*. Without this,
    an earlier selection's mutation can hide the neighbor a later split
    expects to find."""
    if fix == "set_to_zero":
        before = float(df.at[det.row_idx, det.column])
        df.at[det.row_idx, det.column] = 0.0
        entries = [_change_entry(det, det.column, before, 0.0, fix, attribution, flagged=False)]
        # Refund attribution: walk backward from the refund column, absorbing
        # from positive cells until the reversal magnitude is exhausted. The
        # detector guarantees enough cumulative balance exists, so the loop
        # always completes. Most-recent-first matching mirrors how a refund
        # reverses the closest prior sale activity.
        if attribution == "refund":
            remaining = -before  # positive magnitude to absorb
            refund_idx = measure_columns.index(det.column)
            for i in range(refund_idx - 1, -1, -1):
                if remaining <= 0:
                    break
                prev_col = measure_columns[i]
                prev_val = float(df.at[det.row_idx, prev_col])
                if prev_val <= 0:
                    continue
                absorbed = min(prev_val, remaining)
                new_val = prev_val - absorbed
                df.at[det.row_idx, prev_col] = new_val
                entries.append(
                    _change_entry(det, prev_col, prev_val, new_val, fix, attribution, flagged=False)
                )
                remaining -= absorbed
        return entries

    if fix == "split_evenly":
        # Find the zero neighbor — could be either side. Prefer the next
        # column so left-to-right reading order matches the audit entries
        # for the common (X, 0) case; fall back to the previous column for
        # last-column spikes adjacent to a zero on the left (DBL-05).
        partner_col = _zero_neighbor(original, det, measure_columns)
        if partner_col is None:
            raise InvalidSelectionError(
                f"Cannot split: '{det.column}' has no zero neighbor"
            )
        x = float(df.at[det.row_idx, det.column])
        y = float(df.at[det.row_idx, partner_col])
        earlier, later = _split_evenly(x)
        df.at[det.row_idx, det.column] = earlier
        df.at[det.row_idx, partner_col] = later
        # Order entries left-to-right so the audit reads in time order.
        spike_idx = measure_columns.index(det.column)
        partner_idx = measure_columns.index(partner_col)
        if spike_idx < partner_idx:
            entries = [
                _change_entry(det, det.column, x, earlier, fix, attribution, flagged=False),
                _change_entry(det, partner_col, y, later, fix, attribution, flagged=False),
            ]
        else:
            entries = [
                _change_entry(det, partner_col, y, later, fix, attribution, flagged=False),
                _change_entry(det, det.column, x, earlier, fix, attribution, flagged=False),
            ]
        return entries

    if fix == "keep_as_is":
        # No value change; emit an audit entry so the analyst's review is
        # recorded ("I saw this, chose not to change it").
        before = float(df.at[det.row_idx, det.column])
        return [_change_entry(det, det.column, before, before, fix, attribution, flagged=True)]

    raise InvalidSelectionError(f"Unknown fix: {fix}")


def _split_evenly(x: float) -> tuple[float, float]:
    """Split ``x`` between two cells, favoring the earlier on odd integers.

    ``101 -> (51, 50)``  (DBL-09)
    ``100 -> (50, 50)``
    ``100.5 -> (50.25, 50.25)``  (non-integer floats split exactly evenly)
    """
    if x == int(x):
        n = int(x)
        return (float((n + 1) // 2), float(n // 2))
    half = x / 2.0
    return (half, half)


def _next_column(col: str, measure_columns: list[str]) -> str | None:
    try:
        i = measure_columns.index(col)
    except ValueError:
        return None
    if i + 1 >= len(measure_columns):
        return None
    return measure_columns[i + 1]


def _zero_neighbor(
    df: pd.DataFrame,
    det: Detection,
    measure_columns: list[str],
) -> str | None:
    """Return the adjacent measure column whose cell equals 0.0 for the spike's
    row. Prefers the next column so the common (X, 0) case keeps reading
    left-to-right; falls back to the previous column for last-column spikes."""
    try:
        i = measure_columns.index(det.column)
    except ValueError:
        return None
    if i + 1 < len(measure_columns):
        nxt = measure_columns[i + 1]
        if float(df.at[det.row_idx, nxt]) == 0.0:
            return nxt
    if i > 0:
        prev = measure_columns[i - 1]
        if float(df.at[det.row_idx, prev]) == 0.0:
            return prev
    return None


def _prev_column(col: str, measure_columns: list[str]) -> str | None:
    try:
        i = measure_columns.index(col)
    except ValueError:
        return None
    if i == 0:
        return None
    return measure_columns[i - 1]


# ---------------------------------------------------------------------------
# Audit entry construction
# ---------------------------------------------------------------------------


def _change_entry(
    det: Detection,
    column: str,
    before: float,
    after: float,
    fix: SuggestedFix,
    attribution: AnomalyType,
    *,
    flagged: bool,
) -> dict[str, Any]:
    return {
        "change_id": str(uuid.uuid4()),
        "anomaly_type": attribution,
        "row_key": det.row_key,
        "column": column,
        "value_before": before,
        "value_after": after,
        "suggested_fix": fix,
        "flagged": flagged,
    }


def _attribute(
    det: Detection, fix: SuggestedFix, explicit: AnomalyType | None = None,
) -> AnomalyType:
    # If the caller named a detector (e.g., user staged from the Refunds tab),
    # honor it as long as that detector actually flagged the cell. Otherwise
    # fall back to the priority order in ``_FIX_DETECTORS``.
    if explicit is not None and explicit in det.flagged_by:
        return explicit
    candidates = _FIX_DETECTORS.get(fix, [])
    for d in candidates:
        if d in det.flagged_by:
            return d
    # Fallback: first detector that flagged the cell. Reached only when the
    # caller passes a fix the cell's detectors don't actually suggest, which
    # ``apply_selections`` validates against — defensive only.
    return det.flagged_by[0]


def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    # Second precision, trailing "Z" — matches DATA_MODEL.md examples.
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
