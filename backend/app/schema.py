"""Schema validation and column-role inference.

The validation step sits between Parquet parse and detection. It does two
things:

1. **Infer column roles.** Each column is either an identifier (string
   non-time), a time column (name matches one of the format regexes), or a
   measure (numeric data). Time and measure usually overlap — in a typical
   cube the monthly columns are both time-named and numeric.

2. **Run hard checks and soft warnings.** Hard checks block detection;
   soft warnings are surfaced but allow the user to proceed. See
   TEST_SCENARIOS.md §4 for the full matrix (SCH-01..23).

The caller can either accept the inferred roles or override them
(``validate_with_overrides``). Overrides re-run the same validation against
the user's chosen role assignment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

TimeFormat = Literal["YYYY_M", "YYYY-MM", "Mon-YY", "YYYYQn"]

_MONTH_ABBR_TO_NUM = {
    name: i for i, name in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        start=1,
    )
}

_TIME_PATTERNS: dict[TimeFormat, re.Pattern[str]] = {
    "YYYY_M": re.compile(r"^\d{4}_\d{1,2}$"),
    "YYYY-MM": re.compile(r"^\d{4}-(0[1-9]|1[0-2])$"),
    "Mon-YY": re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{2}$"),
    "YYYYQn": re.compile(r"^\d{4}Q[1-4]$"),
}


@dataclass
class ColumnRoles:
    id_columns: list[str] = field(default_factory=list)
    time_columns: list[str] = field(default_factory=list)
    measure_columns: list[str] = field(default_factory=list)


@dataclass
class SchemaResult:
    roles: ColumnRoles
    hard_errors: list[str] = field(default_factory=list)
    soft_warnings: list[str] = field(default_factory=list)
    time_format: TimeFormat | None = None
    # Columns that look like time periods but have some non-numeric values
    # blocking inclusion as measures. The UI surfaces a "Coerce" action so
    # the user can convert with one click — see /coerce route.
    coercible_columns: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.hard_errors


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def infer_schema(df: pd.DataFrame) -> SchemaResult:
    """Auto-detect roles and run all checks against the inferred assignment."""
    structural = _structural_checks(df)
    if structural:
        return SchemaResult(roles=ColumnRoles(), hard_errors=structural)

    roles, time_format, mixed_formats, was_out_of_order = _infer_roles(df)
    result = _validate(df, roles, time_format, mixed_formats, was_out_of_order)
    _annotate_coercible(df, result)
    return result


def _annotate_coercible(df: pd.DataFrame, result: SchemaResult) -> None:
    """Populate ``coercible_columns`` and add a matching observation. Called
    from both the auto-detect and override paths so the UI's Coerce button
    appears regardless of how the schema was determined."""
    result.coercible_columns = _find_coercible_columns(df, result.roles)
    if result.coercible_columns:
        cols = ", ".join(f"'{c}'" for c in result.coercible_columns)
        result.soft_warnings.append(
            f"Column {cols} has a few text cells — left out of the analysis. Fix it below to include."
        )


def _find_coercible_columns(df: pd.DataFrame, roles: ColumnRoles) -> list[str]:
    """Return time-named columns that didn't classify as measures because
    of a small number of non-numeric values. Threshold: <10% of values would
    be lost on coercion. The UI offers a one-click 'Coerce' action for these.
    """
    time_set = set(roles.time_columns)
    measure_set = set(roles.measure_columns)
    coercible: list[str] = []
    for col in df.columns:
        if col not in time_set or col in measure_set:
            continue
        series = df[col]
        if pd.api.types.is_numeric_dtype(series):
            continue
        coerced = pd.to_numeric(series, errors="coerce")
        existing_na = int(series.isna().sum())
        coerced_na = int(coerced.isna().sum())
        new_nans = coerced_na - existing_na
        if new_nans <= 0:
            continue  # nothing was lost — should already be a measure
        if new_nans / max(len(series), 1) < 0.10:
            coercible.append(col)
    return coercible


def validate_with_overrides(df: pd.DataFrame, roles: ColumnRoles) -> SchemaResult:
    """Validate a user-supplied role assignment instead of inferring it."""
    structural = _structural_checks(df)
    if structural:
        return SchemaResult(roles=roles, hard_errors=structural)

    # Detect time format from whatever the user labelled as time.
    fmt, mixed = _detect_time_format(roles.time_columns)

    # Honor the user's column order — they may deliberately want a specific
    # sequence — but still report if it isn't chronological.
    was_out_of_order = False
    if fmt is not None:
        sorted_time = _sort_time_columns(roles.time_columns, fmt)
        was_out_of_order = sorted_time != roles.time_columns

    result = _validate(df, roles, fmt, mixed, was_out_of_order)
    _annotate_coercible(df, result)
    return result


# ---------------------------------------------------------------------------
# Structural checks (file-level, independent of role assignment)
# ---------------------------------------------------------------------------


def _structural_checks(df: pd.DataFrame) -> list[str]:
    errors: list[str] = []

    # SCH-15: duplicate column names
    duplicated = df.columns[df.columns.duplicated()].tolist()
    if duplicated:
        errors.append(f"Duplicate column names: {sorted(set(duplicated))}")

    # UP-07: empty file
    if df.shape[0] == 0:
        errors.append("File has no data rows")
    if df.shape[1] == 0:
        errors.append("File has no columns")

    return errors


# ---------------------------------------------------------------------------
# Role inference
# ---------------------------------------------------------------------------


def _detect_time_format(columns: list[str]) -> tuple[TimeFormat | None, bool]:
    """Return ``(format, mixed)`` where ``mixed`` indicates >1 format matched."""
    matches: dict[TimeFormat, int] = {}
    for col in columns:
        for fmt, pat in _TIME_PATTERNS.items():
            if pat.match(str(col)):
                matches[fmt] = matches.get(fmt, 0) + 1
                break
    if not matches:
        return None, False
    primary = max(matches, key=lambda f: matches[f])
    return primary, len(matches) > 1


def _is_time_named(col: str) -> bool:
    return any(p.match(str(col)) for p in _TIME_PATTERNS.values())


def _is_numeric_column(series: pd.Series) -> bool:
    if pd.api.types.is_numeric_dtype(series):
        return True
    # Object/string column that fully coerces to numeric is treated as numeric.
    coerced = pd.to_numeric(series, errors="coerce")
    # Allow NaNs that already existed as NaN, but any value that became NaN
    # only after coercion means a non-numeric string was present.
    original_na = series.isna()
    coerced_na = coerced.isna()
    return bool((coerced_na == original_na).all())


def _infer_roles(df: pd.DataFrame) -> tuple[ColumnRoles, TimeFormat | None, bool, bool]:
    id_cols: list[str] = []
    time_cols: list[str] = []
    measure_cols: list[str] = []

    for col in df.columns:
        is_time = _is_time_named(col)
        is_numeric = _is_numeric_column(df[col])

        if is_time:
            time_cols.append(col)
            if is_numeric:
                measure_cols.append(col)
        elif is_numeric:
            # Numeric but not time-named — still a measure (e.g., "total_revenue").
            measure_cols.append(col)
        else:
            id_cols.append(col)

    fmt, mixed = _detect_time_format(time_cols)

    # Auto-sort time columns chronologically — critical for the
    # double-booking detector, which looks at adjacent columns. Whether the
    # original file was out of order is reported as a soft warning so the
    # user knows it happened.
    was_out_of_order = False
    if fmt is not None:
        sorted_time = _sort_time_columns(time_cols, fmt)
        if sorted_time != time_cols:
            was_out_of_order = True
            # Reorder the time-named entries in measure_cols to match.
            time_set = set(time_cols)
            measure_cols = [
                *_sort_measures_by_time(measure_cols, time_set, sorted_time),
            ]
            time_cols = sorted_time

    return (
        ColumnRoles(id_columns=id_cols, time_columns=time_cols, measure_columns=measure_cols),
        fmt,
        mixed,
        was_out_of_order,
    )


def _sort_measures_by_time(
    measure_cols: list[str],
    time_set: set[str],
    sorted_time: list[str],
) -> list[str]:
    """Reorder time-named measures into chronological order, leaving non-time
    measures in their original position."""
    # Walk the original list, replacing each time-named slot with the next
    # chronologically-sorted time column. Non-time measures pass through.
    time_iter = iter(sorted_time)
    out: list[str] = []
    for c in measure_cols:
        if c in time_set:
            out.append(next(time_iter))
        else:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(
    df: pd.DataFrame,
    roles: ColumnRoles,
    time_format: TimeFormat | None,
    mixed_formats: bool,
    was_out_of_order: bool = False,
) -> SchemaResult:
    result = SchemaResult(roles=roles, time_format=time_format)

    # --- Hard checks ----------------------------------------------------
    # SCH-10
    if not roles.id_columns:
        result.hard_errors.append("No identifier columns detected")
    # SCH-12
    if not roles.time_columns:
        result.hard_errors.append("No time columns detected")
    # SCH-11: downgraded from hard fail to observation. File parses fine,
    # but with no measure columns the detectors have nothing to do — surface
    # that fact rather than blocking.
    no_measures = not roles.measure_columns

    # SCH-14: every measure column must coerce to numeric
    for col in roles.measure_columns:
        if col not in df.columns:
            result.hard_errors.append(f"Measure column '{col}' not in file")
            continue
        if not _is_numeric_column(df[col]):
            sample = _first_bad_numeric_sample(df[col])
            result.hard_errors.append(
                f"Measure column '{col}' contains non-numeric data (sample: {sample!r})"
            )

    # SCH-13: duplicate ID-tuple rows (only meaningful if IDs present)
    if roles.id_columns and all(c in df.columns for c in roles.id_columns):
        dup_mask = df.duplicated(subset=roles.id_columns, keep=False)
        if dup_mask.any():
            n_dup = int(dup_mask.sum())
            sample_dups = (
                df.loc[dup_mask, roles.id_columns]
                .drop_duplicates()
                .head(3)
                .to_dict("records")
            )
            result.hard_errors.append(
                f"Duplicate identifier rows ({n_dup} affected); sample tuples: {sample_dups}"
            )

    # Don't bother computing soft warnings if hard checks already failed —
    # the user will see them after fixing structure.
    if result.hard_errors:
        return result

    # --- Soft warnings --------------------------------------------------
    # SCH-11 (downgraded): no measure columns to analyze.
    if no_measures:
        result.soft_warnings.append(
            "No sales numbers found in this file — only identifiers and labels. "
            "You can continue, but anomaly detection will have nothing to check. "
            "If you expected sales values, you may have the wrong file."
        )

    # SCH-05: mixed time formats
    if mixed_formats:
        result.soft_warnings.append("Time columns mix multiple formats")

    # SCH-06: only one ID column
    if len(roles.id_columns) == 1:
        result.soft_warnings.append(
            f"Only one identifier column ('{roles.id_columns[0]}'). "
            f"If your file has multiple rows per {roles.id_columns[0]}, "
            f"they'll be treated as duplicates and block detection. "
            f"Promote another column (e.g. product line) to identifier if needed."
        )

    # SCH-20: time-sequence gap
    if time_format is not None:
        gaps = _find_time_gaps(roles.time_columns, time_format)
        if gaps:
            labels = [_format_time(g, time_format) for g in gaps[:5]]
            result.soft_warnings.append(
                f"Missing time period(s) in the sequence: {', '.join(labels)}"
                + (f" (+{len(gaps) - 5} more)" if len(gaps) > 5 else "")
            )

    # SCH-21: >90% null columns. Only warn for columns that are actually
    # in play — excluded columns shouldn't pollute the notices list.
    in_use = set(roles.id_columns) | set(roles.time_columns) | set(roles.measure_columns)
    for col in df.columns:
        if col not in in_use:
            continue
        null_frac = df[col].isna().mean()
        if null_frac > 0.9:
            result.soft_warnings.append(
                f"Column '{col}' is {null_frac:.0%} null"
            )

    # SCH-22: all-zero rows
    if roles.measure_columns:
        measure = df[roles.measure_columns].apply(pd.to_numeric, errors="coerce").fillna(0)
        all_zero = (measure == 0).all(axis=1)
        if all_zero.any():
            n = int(all_zero.sum())
            result.soft_warnings.append(
                f"{n} row{'' if n == 1 else 's'} have zero in every period — no sales activity at all. "
                f"Often blank entries left in by accident. Detection will skip them."
            )

    # SCH-23: time columns were out of chronological order — already
    # reordered above. Flag it so the user knows what happened.
    if was_out_of_order:
        result.soft_warnings.append(
            "Your time periods weren't in chronological order. We sorted them "
            "automatically so period-over-period checks work correctly. The source "
            "file is unchanged."
        )

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _first_bad_numeric_sample(series: pd.Series) -> object:
    coerced = pd.to_numeric(series, errors="coerce")
    bad_mask = coerced.isna() & ~series.isna()
    if not bad_mask.any():
        return None
    return series[bad_mask].iloc[0]


def _parse_time(col: str, fmt: TimeFormat) -> int | None:
    """Return an absolute period index (months or quarters) for sorting."""
    s = str(col)
    if fmt == "YYYY_M" and _TIME_PATTERNS["YYYY_M"].match(s):
        year, month = s.split("_")
        return int(year) * 12 + int(month) - 1
    if fmt == "YYYY-MM" and _TIME_PATTERNS["YYYY-MM"].match(s):
        year, month = s.split("-")
        return int(year) * 12 + int(month) - 1
    if fmt == "Mon-YY" and _TIME_PATTERNS["Mon-YY"].match(s):
        mon, year = s.split("-")
        return (2000 + int(year)) * 12 + _MONTH_ABBR_TO_NUM[mon] - 1
    if fmt == "YYYYQn" and _TIME_PATTERNS["YYYYQn"].match(s):
        year, q = s.split("Q")
        return int(year) * 4 + int(q) - 1
    return None


def _find_time_gaps(cols: list[str], fmt: TimeFormat) -> list[int]:
    indices = sorted(i for i in (_parse_time(c, fmt) for c in cols) if i is not None)
    if len(indices) < 2:
        return []
    full = set(range(indices[0], indices[-1] + 1))
    return sorted(full - set(indices))


def _format_time(index: int, fmt: TimeFormat) -> str:
    """Inverse of ``_parse_time``: turn an absolute period index back into a
    human-readable column label. Used to render gap warnings."""
    if fmt == "YYYYQn":
        year, q = divmod(index, 4)
        return f"{year}Q{q + 1}"
    year, month = divmod(index, 12)
    month_num = month + 1
    if fmt == "YYYY_M":
        return f"{year}_{month_num}"
    if fmt == "YYYY-MM":
        return f"{year}-{month_num:02d}"
    if fmt == "Mon-YY":
        names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        return f"{names[month_num - 1]}-{year % 100:02d}"
    return str(index)


def _sort_time_columns(cols: list[str], fmt: TimeFormat) -> list[str]:
    keyed = [(c, _parse_time(c, fmt)) for c in cols]
    # Stable sort; columns that don't parse stay at the end in original order.
    return [c for c, _ in sorted(keyed, key=lambda x: (x[1] is None, x[1] or 0))]
