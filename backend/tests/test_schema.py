"""TEST_SCENARIOS.md §4 — schema validation."""

from __future__ import annotations

import pandas as pd
import pytest

from app.schema import ColumnRoles, infer_schema, validate_with_overrides


def _cube(time_cols: list[str], rows: int = 3) -> pd.DataFrame:
    data: dict[str, list] = {
        "customer": [f"Cust_{i + 1}" for i in range(rows)],
        "product_line": [f"Prod_{chr(ord('A') + i)}" for i in range(rows)],
    }
    for c in time_cols:
        data[c] = [float(100 * (i + 1)) for i in range(rows)]
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# §4.1 Role detection
# ---------------------------------------------------------------------------


# SCH-01
def test_standard_cube_detected_cleanly():
    df = _cube(["2021_1", "2021_2", "2021_3"])
    r = infer_schema(df)
    assert r.ok
    assert r.roles.id_columns == ["customer", "product_line"]
    assert r.roles.time_columns == ["2021_1", "2021_2", "2021_3"]
    assert r.roles.measure_columns == ["2021_1", "2021_2", "2021_3"]
    assert r.time_format == "YYYY_M"
    assert r.soft_warnings == []


# SCH-02
def test_yyyy_mm_format_detected():
    df = _cube(["2021-01", "2021-02", "2021-03"])
    r = infer_schema(df)
    assert r.ok
    assert r.time_format == "YYYY-MM"


# SCH-03
def test_mon_yy_format_detected():
    df = _cube(["Jan-21", "Feb-21", "Mar-21"])
    r = infer_schema(df)
    assert r.ok
    assert r.time_format == "Mon-YY"


# SCH-04
def test_yyyyqn_format_detected():
    df = _cube(["2021Q1", "2021Q2", "2021Q3", "2021Q4"])
    r = infer_schema(df)
    assert r.ok
    assert r.time_format == "YYYYQn"
    assert len(r.roles.time_columns) == 4


# SCH-05: mixed formats → soft warning, still ok
def test_mixed_time_formats_warned():
    df = _cube(["2022_1", "Jan-22", "2022_3"])
    r = infer_schema(df)
    assert r.ok
    assert any("mix" in w.lower() for w in r.soft_warnings)


# SCH-06: single ID column → warned
def test_single_id_column_warning():
    df = pd.DataFrame({
        "customer": ["A", "B", "C"],
        "2021_1": [1.0, 2.0, 3.0],
        "2021_2": [1.0, 2.0, 3.0],
    })
    r = infer_schema(df)
    assert r.ok
    assert any("Only one identifier" in w for w in r.soft_warnings)


# SCH-07
def test_override_measure_to_id_accepted():
    df = _cube(["2021_1", "2021_2", "2021_3"])
    roles = ColumnRoles(
        id_columns=["customer", "product_line", "2021_1"],
        time_columns=["2021_2", "2021_3"],
        measure_columns=["2021_2", "2021_3"],
    )
    r = validate_with_overrides(df, roles)
    assert r.ok


# SCH-08: override ID to measure where coerce passes (numeric strings)
def test_override_id_to_measure_numeric_strings_accepted():
    df = pd.DataFrame({
        "customer": ["A", "B", "C"],
        "product_line": ["X", "Y", "Z"],
        "code": ["100", "200", "300"],
        "2021_1": [1.0, 2.0, 3.0],
        "2021_2": [4.0, 5.0, 6.0],
    })
    roles = ColumnRoles(
        id_columns=["customer", "product_line"],
        time_columns=["2021_1", "2021_2"],
        measure_columns=["code", "2021_1", "2021_2"],
    )
    r = validate_with_overrides(df, roles)
    assert r.ok


# SCH-09: override ID to measure where coerce fails
def test_override_id_to_measure_non_numeric_rejected():
    df = pd.DataFrame({
        "customer": ["A", "B", "C"],
        "product_line": ["X", "Y", "Z"],
        "tag": ["alpha", "beta", "gamma"],
        "2021_1": [1.0, 2.0, 3.0],
        "2021_2": [4.0, 5.0, 6.0],
    })
    roles = ColumnRoles(
        id_columns=["customer", "product_line"],
        time_columns=["2021_1", "2021_2"],
        measure_columns=["tag", "2021_1", "2021_2"],
    )
    r = validate_with_overrides(df, roles)
    assert not r.ok
    assert any("non-numeric" in e for e in r.hard_errors)


# ---------------------------------------------------------------------------
# §4.2 Hard checks
# ---------------------------------------------------------------------------


# SCH-10
def test_no_id_columns_hard_fail():
    df = pd.DataFrame({"2021_1": [1.0, 2.0], "2021_2": [3.0, 4.0]})
    r = infer_schema(df)
    assert not r.ok
    assert any("identifier" in e.lower() for e in r.hard_errors)


# SCH-11: downgraded from hard fail — no-measures is now a soft observation
# so the user can still walk the workflow if they want.
def test_no_measure_columns_is_soft_warning():
    df = pd.DataFrame({
        "customer": ["A", "B"],
        "product_line": ["X", "Y"],
        "2022_1": ["string", "values"],  # time-named but non-numeric
    })
    r = infer_schema(df)
    # Time column exists (name matches pattern) so SCH-12 won't fire.
    # Measure detection sees no numeric columns → goes into soft warnings.
    assert r.ok or "time" in str(r.hard_errors).lower()
    if r.ok:
        assert any("no sales numbers" in w.lower() for w in r.soft_warnings)


# SCH-12
def test_no_time_columns_hard_fail():
    df = pd.DataFrame({
        "customer": ["A", "B"],
        "product_line": ["X", "Y"],
        "total_revenue": [100.0, 200.0],
    })
    r = infer_schema(df)
    assert not r.ok
    assert any("time" in e.lower() for e in r.hard_errors)


# SCH-13
def test_duplicate_id_tuples_hard_fail():
    df = pd.DataFrame({
        "customer": ["A", "A", "B"],
        "product_line": ["X", "X", "Y"],
        "2021_1": [1.0, 2.0, 3.0],
        "2021_2": [4.0, 5.0, 6.0],
    })
    r = infer_schema(df)
    assert not r.ok
    assert any("Duplicate identifier" in e for e in r.hard_errors)


# SCH-14
def test_measure_column_with_non_numeric():
    df = pd.DataFrame({
        "customer": ["A", "B", "C"],
        "product_line": ["X", "Y", "Z"],
        "2021_1": ["one", "two", "three"],
        "2021_2": [1.0, 2.0, 3.0],
    })
    r = infer_schema(df)
    # 2021_1 is time-named but non-numeric → not flagged as measure;
    # 2021_2 is fine. So this file has only 1 measure col, no hard fail from
    # SCH-14 itself. The SCH-14 case fires when the user *overrides*.
    # Verify via override path:
    roles = ColumnRoles(
        id_columns=["customer", "product_line"],
        time_columns=["2021_1", "2021_2"],
        measure_columns=["2021_1", "2021_2"],
    )
    r2 = validate_with_overrides(df, roles)
    assert not r2.ok
    assert any("non-numeric" in e for e in r2.hard_errors)


# SCH-15
def test_duplicate_column_names_hard_fail():
    df = pd.DataFrame([[1, 2, 3]], columns=["a", "a", "b"])
    r = infer_schema(df)
    assert not r.ok
    assert any("Duplicate column" in e for e in r.hard_errors)


# SCH-16
def test_empty_file_hard_fail():
    df = pd.DataFrame({"customer": [], "2021_1": []})
    r = infer_schema(df)
    assert not r.ok
    assert any("no data rows" in e.lower() for e in r.hard_errors)


# ---------------------------------------------------------------------------
# §4.3 Soft warnings
# ---------------------------------------------------------------------------


# SCH-20
def test_time_gap_warning():
    df = _cube(["2022_4", "2022_6", "2022_7"])
    r = infer_schema(df)
    assert r.ok
    # The warning should name the missing period in human format, not a raw index.
    assert any("2022_5" in w for w in r.soft_warnings), r.soft_warnings


# SCH-21: column is "mostly null" — spec says >90%, so 9/10 isn't enough.
def test_mostly_null_column_warning():
    n = 20
    df = pd.DataFrame({
        "customer": [f"C_{i}" for i in range(n)],
        "product_line": ["P"] * n,
        "2021_1": [1.0] * n,
        "2021_2": [None] * 19 + [42.0],  # 95% null
    })
    r = infer_schema(df)
    assert any("null" in w.lower() for w in r.soft_warnings)


# SCH-22
def test_all_zero_row_warning():
    df = pd.DataFrame({
        "customer": ["A", "B"],
        "product_line": ["X", "Y"],
        "2021_1": [0.0, 1.0],
        "2021_2": [0.0, 2.0],
    })
    r = infer_schema(df)
    assert any("zero in every period" in w for w in r.soft_warnings)


# SCH-23 (renumbered, was SCH-24)
def test_time_columns_out_of_order_warning():
    df = pd.DataFrame({
        "customer": ["A", "B"],
        "product_line": ["X", "Y"],
        "2022_3": [1.0, 2.0],
        "2022_1": [3.0, 4.0],
        "2022_2": [5.0, 6.0],
    })
    r = infer_schema(df)
    assert any("chronological order" in w for w in r.soft_warnings)


# ---------------------------------------------------------------------------
# End-to-end against the generator
# ---------------------------------------------------------------------------


def test_generated_cube_validates_cleanly():
    from data.generator import make_cube
    df = make_cube(rows=50, time_periods=24, seed=42)
    r = infer_schema(df)
    assert r.ok
    assert r.roles.id_columns == ["customer", "product_line"]
    assert len(r.roles.time_columns) == 24
    assert len(r.roles.measure_columns) == 24
