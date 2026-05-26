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
    """Apply selected fixes and return cleaned DataFrame + audit dict.

    Bulked for large staged sets. The naive path was a ``df.at[r, c] = v``
    per selection, which on 923k selections costs ~5s and churns a lot of
    intermediate scalars. We split selections into four buckets by fix
    type and process the dominant ``set_to_zero`` (non-refund) case via
    a single ``df.loc[rows, col] = 0`` per column — roughly ~30 bulk
    writes instead of ~900k single-cell writes. Refund cascades and
    splits retain per-cell handling because they're inherently sequential
    and the count is small.
    """
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

    # Pass 1: validate + attribute + bucket by (fix, needs-cascade).
    # We keep small per-bucket lists rather than tagging each selection
    # so the inner loops aren't doing per-iteration branching.
    set_to_zero_simple: list[tuple[Detection, AnomalyType]] = []
    set_to_zero_refund: list[tuple[Detection, AnomalyType]] = []
    splits: list[tuple[Detection, AnomalyType]] = []
    keeps: list[tuple[Detection, AnomalyType]] = []
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
        if sel.fix == "set_to_zero":
            if attribution == "refund":
                set_to_zero_refund.append((det, attribution))
            else:
                set_to_zero_simple.append((det, attribution))
        elif sel.fix == "split_evenly":
            splits.append((det, attribution))
        elif sel.fix == "keep_as_is":
            keeps.append((det, attribution))
        else:
            raise InvalidSelectionError(f"Unknown fix: {sel.fix}")

    # Pass 2: bulk set_to_zero by column. Reading and writing in one call
    # per column instead of per cell. Non-refund only — refund attribution
    # also needs the cascade, which is handled in pass 3.
    by_col: dict[str, list[tuple[Detection, AnomalyType]]] = {}
    for det, attribution in set_to_zero_simple:
        by_col.setdefault(det.column, []).append((det, attribution))
    for col, items in by_col.items():
        row_idxs = [det.row_idx for det, _ in items]
        before_vals = df[col].iloc[row_idxs].tolist()
        cleaned.loc[row_idxs, col] = 0.0
        for (det, attribution), before in zip(items, before_vals, strict=True):
            changes.append(_change_entry(
                det, col, float(before), 0.0, "set_to_zero", attribution,
                flagged=False, change_kind="primary",
            ))
            summary[attribution] += 1

    # Pass 3: refund cells + cascades. Per-cell because each cascade walks
    # backward through the same row, mutating earlier periods until the
    # reversal is absorbed. Reads from ``cleaned`` so previously-applied
    # mutations (including non-refund set_to_zero from pass 2) are visible
    # — matches the original interleaved behavior.
    for det, attribution in set_to_zero_refund:
        before = float(cleaned.at[det.row_idx, det.column])
        cleaned.at[det.row_idx, det.column] = 0.0
        primary_id = str(uuid.uuid4())
        changes.append(_change_entry(
            det, det.column, before, 0.0, "set_to_zero", attribution,
            flagged=False, change_kind="primary", change_id=primary_id,
        ))
        summary[attribution] += 1
        remaining = -before
        refund_idx = measure_columns.index(det.column)
        for i in range(refund_idx - 1, -1, -1):
            if remaining <= 0:
                break
            prev_col = measure_columns[i]
            prev_val = float(cleaned.at[det.row_idx, prev_col])
            if prev_val <= 0:
                continue
            absorbed = min(prev_val, remaining)
            new_val = prev_val - absorbed
            cleaned.at[det.row_idx, prev_col] = new_val
            changes.append(_change_entry(
                det, prev_col, prev_val, new_val, "set_to_zero", attribution,
                flagged=False, change_kind="refund_cascade",
                parent_change_id=primary_id,
            ))
            summary[attribution] += 1
            remaining -= absorbed

    # Pass 4: split_evenly. Per-cell because each split touches two cells
    # and needs the zero-neighbor lookup against the original df.
    for det, attribution in splits:
        partner_col = _zero_neighbor(df, det, measure_columns)
        if partner_col is None:
            raise InvalidSelectionError(
                f"Cannot split: '{det.column}' has no zero neighbor"
            )
        x = float(cleaned.at[det.row_idx, det.column])
        y = float(cleaned.at[det.row_idx, partner_col])
        earlier, later = _split_evenly(x)
        cleaned.at[det.row_idx, det.column] = earlier
        cleaned.at[det.row_idx, partner_col] = later
        spike_idx = measure_columns.index(det.column)
        partner_idx = measure_columns.index(partner_col)
        if spike_idx < partner_idx:
            changes.append(_change_entry(
                det, det.column, x, earlier, "split_evenly", attribution, flagged=False,
            ))
            changes.append(_change_entry(
                det, partner_col, y, later, "split_evenly", attribution, flagged=False,
            ))
        else:
            changes.append(_change_entry(
                det, partner_col, y, later, "split_evenly", attribution, flagged=False,
            ))
            changes.append(_change_entry(
                det, det.column, x, earlier, "split_evenly", attribution, flagged=False,
            ))
        summary[attribution] += 2

    # Pass 5: keep_as_is. No mutation, just an audit entry recording that
    # the analyst reviewed and chose not to change.
    for det, attribution in keeps:
        before = float(cleaned.at[det.row_idx, det.column])
        changes.append(_change_entry(
            det, det.column, before, before, "keep_as_is", attribution, flagged=True,
        ))
        summary[attribution] += 1

    audit = {
        "file_id": file_id,
        "applied_at": _iso_utc(applied_at),
        "user_id": f"project:{project_slug}",
        "summary": summary,
        "changes": changes,
    }
    return ApplyResult(cleaned_df=cleaned, audit=audit)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


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
    change_kind: str = "primary",
    parent_change_id: str | None = None,
    change_id: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "change_id": change_id or str(uuid.uuid4()),
        "anomaly_type": attribution,
        "change_kind": change_kind,
        "row_key": det.row_key,
        "column": column,
        "value_before": before,
        "value_after": after,
        "suggested_fix": fix,
        "flagged": flagged,
    }
    if parent_change_id is not None:
        entry["parent_change_id"] = parent_change_id
    return entry


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
