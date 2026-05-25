"""Generate a varied pack of cubes for manual UI testing.

Filenames are prefixed with the **test ID** they exercise (e.g. ``up01_``
for the happy-path upload, ``sch20_`` for the time-gap soft warning).
That way the file you drop into the UI and the row in TEST_SCENARIOS.md
share a name.

Run from the repo root:

    uv run --project backend python data/build_test_fixtures.py

Writes everything into ``data/test_fixtures/``. Each line of stdout names
the fixture, its test ID(s), and a one-line description.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.generator import make_cube  # noqa: E402

OUT = REPO_ROOT / "data" / "test_fixtures"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # ---- Valid cubes: each filename names the primary test row it covers.
    valid_fixtures: list[tuple[str, str, pd.DataFrame]] = [
        (
            "up01_clean.parquet",
            "UP-01 · happy-path upload (also SCH-01 standard cube). No INJECTED anomalies — sparse rows still trigger natural double-booking/outlier patterns.",
            make_cube(rows=80, time_periods=24, seed=1, sparsity=0.3),
        ),
        (
            "rev01_no_anomalies.parquet",
            "REV-01 · true empty detection state. All cells uniform, no sparsity — no detector fires.",
            _build_uniform_cube(),
        ),
        (
            "demo_mixed_anomalies.parquet",
            "Walking demo. All 4 detectors fire; goes through the whole workflow.",
            make_cube(
                rows=150, time_periods=24, seed=2, sparsity=0.3,
                negatives={"count": 30, "magnitude": "medium"},
                refunds={"count": 12, "style": "mixed"},
                double_bookings={"count": 6},
                outliers={"count": 8},
            ),
        ),
        (
            "neg03_many.parquet",
            "NEG-03 · many negatives. Stress-test the red-flag rendering.",
            make_cube(
                rows=200, time_periods=24, seed=3, sparsity=0.2,
                negatives={"count": 200, "magnitude": "large"},
            ),
        ),
        (
            "ref02_round_reversals.parquet",
            "REF-02 · round-number refund pattern (confidence ≈ 0.6).",
            make_cube(
                rows=100, time_periods=24, seed=4, sparsity=0.2,
                refunds={"count": 25, "style": "round"},
            ),
        ),
        (
            "dbl04_many_pairs.parquet",
            "DBL-04 · many (spike, 0) pairs. Split-vs-remove choices in Review.",
            make_cube(
                rows=120, time_periods=24, seed=5, sparsity=0.3,
                double_bookings={"count": 18},
            ),
        ),
        (
            "out_storm.parquet",
            "Outlier-heavy. Filter chips and dim/active states get a workout.",
            make_cube(
                rows=120, time_periods=24, seed=6, sparsity=0.4,
                outliers={"count": 30},
            ),
        ),

        # ---- Time-format variants — exercise SCH-02..04 in the UI ----
        (
            "sch02_yyyy_mm.parquet",
            "SCH-02 · time columns use `YYYY-MM` format (e.g. `2021-01`).",
            make_cube(
                rows=80, time_periods=18, seed=7, sparsity=0.3,
                time_format="YYYY-MM",
                negatives={"count": 10},
            ),
        ),
        (
            "sch03_mon_yy.parquet",
            "SCH-03 · time columns use `Mon-YY` format (e.g. `Jan-21`).",
            make_cube(
                rows=80, time_periods=18, seed=8, sparsity=0.3,
                time_format="Mon-YY",
                negatives={"count": 10},
            ),
        ),
        (
            "sch04_quarterly.parquet",
            "SCH-04 · quarterly columns (e.g. `2021Q1`), 12 quarters.",
            make_cube(
                rows=80, time_periods=12, seed=9, sparsity=0.3,
                time_format="YYYYQn",
                negatives={"count": 8},
            ),
        ),
    ]

    print("== Valid cubes ==")
    for name, note, df in valid_fixtures:
        path = OUT / name
        df.to_parquet(path, index=False, compression="snappy")
        size_kb = path.stat().st_size / 1024
        print(f"  {name}  ({df.shape[0]} rows × {df.shape[1]} cols, {size_kb:.0f} KB)")
        print(f"      {note}")

    # ---- Schema edge cases ----
    print("\n== Schema edge cases (soft warnings) ==")
    for builder in (
        _build_sch20_time_gap,
        _build_sch05_mixed_formats,
        _build_sch06_single_id,
        _build_sch21_mostly_null,
        _build_sch22_all_zero_row,
        _build_sch23_out_of_order,
    ):
        path, note, df = builder()
        df.to_parquet(path, index=False)
        print(f"  {path.name}  ({df.shape[0]} rows × {df.shape[1]} cols)")
        print(f"      {note}")

    # ---- Hard-check fixtures (detection blocked) ----
    # SCH-15 writes its parquet directly inside the builder (duplicate column
    # names need PyArrow's low-level API). Others write here.
    print("\n== Schema hard checks (detection blocked) ==")
    for builder in (
        _build_sch10_no_id,
        _build_sch11_no_measure,
        _build_sch12_no_time,
        _build_sch13_dup_rows,
        _build_sch14_string_in_measure,
    ):
        path, note, df = builder()
        df.to_parquet(path, index=False)
        print(f"  {path.name}  ({df.shape[0]} rows × {df.shape[1]} cols)")
        print(f"      {note}")
    sch15_path, sch15_note, sch15_df = _build_sch15_dup_col_names()
    print(f"  {sch15_path.name}  ({sch15_df.shape[0]} rows × {sch15_df.shape[1]} cols)")
    print(f"      {sch15_note}")

    # ---- Broken-file fixtures (upload error paths) ----
    print("\n== Broken files (upload error paths) ==")
    _build_broken_files(OUT)
    for p in sorted(OUT.glob("up0*")):
        if not p.is_file() or p.suffix == ".parquet" and p.stat().st_size > 1000:
            continue
        # Print broken (small/invalid) ones
        if p.stat().st_size < 1000 or p.suffix not in (".parquet",):
            print(f"  {p.name}  ({p.stat().st_size} bytes)")

    print(f"\nAll fixtures written to {OUT}")
    print(f"To open in Finder: open {OUT}")


def _build_uniform_cube() -> pd.DataFrame:
    """All cells identical positive value, no sparsity.

    - No negatives / refunds (all positive).
    - No double-bookings (no zeros, so no spike-zero neighbors).
    - No IQR outliers (IQR = 0 per row, all values inside the band).

    Produces a true "zero detections" state for REV-01.
    """
    n_rows = 30
    time_cols = [f"2022_{i + 1}" for i in range(12)]
    data: dict[str, list] = {
        "customer": [f"Cust_{i + 1}" for i in range(n_rows)],
        "product_line": [f"Prod_{chr(65 + (i % 10))}" for i in range(n_rows)],
    }
    for tc in time_cols:
        data[tc] = [1000.0] * n_rows
    return pd.DataFrame(data)


def _build_sch20_time_gap() -> tuple[Path, str, pd.DataFrame]:
    """SCH-20: drop one time column from the middle."""
    df = make_cube(rows=80, time_periods=24, seed=10, sparsity=0.3,
                   negatives={"count": 8})
    df = df.drop(columns=["2022_5"])  # creates a gap between 2022_4 and 2022_6
    return (
        OUT / "sch20_time_gap.parquet",
        "SCH-20 · missing time column (`2022_5`). Schema step should show a gap warning.",
        df,
    )


def _build_sch05_mixed_formats() -> tuple[Path, str, pd.DataFrame]:
    """SCH-05: two different time formats in one file."""
    df = make_cube(rows=60, time_periods=12, seed=11, sparsity=0.3,
                   negatives={"count": 5})
    df = df.rename(columns={"2021_6": "Jun-21", "2021_7": "Jul-21"})
    return (
        OUT / "sch05_mixed_formats.parquet",
        "SCH-05 · two time formats mixed (`YYYY_M` + `Mon-YY`). Soft warning expected.",
        df,
    )


def _build_sch06_single_id() -> tuple[Path, str, pd.DataFrame]:
    """SCH-06: only one identifier column."""
    df = make_cube(rows=80, time_periods=18, seed=12, sparsity=0.3,
                   negatives={"count": 6})
    df = df.drop(columns=["product_line"]).drop_duplicates(subset=["customer"])
    return (
        OUT / "sch06_single_id.parquet",
        "SCH-06 · single identifier column. Warning about deduplication risk.",
        df,
    )


def _build_sch21_mostly_null() -> tuple[Path, str, pd.DataFrame]:
    """SCH-21: one measure column is >90% null."""
    df = make_cube(rows=80, time_periods=18, seed=21, sparsity=0.2,
                   negatives={"count": 6})
    # 95% of 2022_3 → NaN
    rng = pd.Series(range(len(df)))
    df.loc[rng < int(len(df) * 0.95), "2022_3"] = None
    return (
        OUT / "sch21_mostly_null.parquet",
        "SCH-21 · `2022_3` is ~95% null. Notice should name the column.",
        df,
    )


def _build_sch22_all_zero_row() -> tuple[Path, str, pd.DataFrame]:
    """SCH-22: one row has zero across every measure column."""
    df = make_cube(rows=80, time_periods=18, seed=22, sparsity=0.3,
                   negatives={"count": 6})
    measure_cols = [c for c in df.columns if c not in ("customer", "product_line")]
    df.loc[5, measure_cols] = 0.0
    return (
        OUT / "sch22_all_zero_row.parquet",
        "SCH-22 · row 5 has zero across every measure. Notice should name the count.",
        df,
    )


def _build_sch23_out_of_order() -> tuple[Path, str, pd.DataFrame]:
    """SCH-23 (renumbered): time columns not in chronological order."""
    df = make_cube(rows=80, time_periods=12, seed=23, sparsity=0.2,
                   negatives={"count": 5})
    id_cols = ["customer", "product_line"]
    time_cols = [c for c in df.columns if c not in id_cols]
    # Shuffle deterministically so the order is clearly wrong.
    mixed = [
        time_cols[6], time_cols[0], time_cols[3], time_cols[1],
        time_cols[8], time_cols[2], time_cols[5], time_cols[4],
        time_cols[10], time_cols[7], time_cols[11], time_cols[9],
    ]
    df = df[id_cols + mixed]
    return (
        OUT / "sch23_out_of_order.parquet",
        "SCH-23 · time columns in non-chronological order. Backend auto-sorts; observation surfaces the fix.",
        df,
    )


def _build_sch10_no_id() -> tuple[Path, str, pd.DataFrame]:
    """SCH-10: only numeric columns, no string IDs."""
    df = make_cube(rows=60, time_periods=12, seed=10, sparsity=0.2,
                   negatives={"count": 4})
    df = df.drop(columns=["customer", "product_line"])
    return (
        OUT / "sch10_no_id.parquet",
        "SCH-10 · no string ID columns at all. Should hard-fail in Schema step.",
        df,
    )


def _build_sch11_no_measure() -> tuple[Path, str, pd.DataFrame]:
    """SCH-11: time-named columns but all string values (no numeric measures).

    Has to include time-named columns so SCH-12 ("no time columns") doesn't
    fire first and block this fixture from exercising SCH-11.
    """
    n = 40
    time_cols = [f"2022_{i + 1}" for i in range(6)]
    data = {
        "customer": [f"Cust_{i + 1}" for i in range(n)],
        "product_line": [f"Prod_{chr(65 + (i % 10))}" for i in range(n)],
    }
    for tc in time_cols:
        data[tc] = [f"label_{i}" for i in range(n)]  # strings, not numbers
    df = pd.DataFrame(data)
    return (
        OUT / "sch11_no_measure.parquet",
        "SCH-11 · time-named columns are strings, no numeric measures. File walks through; detection finds nothing.",
        df,
    )


def _build_sch12_no_time() -> tuple[Path, str, pd.DataFrame]:
    """SCH-12: numeric columns present but none named like a date."""
    df = pd.DataFrame({
        "customer": [f"Cust_{i + 1}" for i in range(40)],
        "product_line": [f"Prod_{chr(65 + (i % 10))}" for i in range(40)],
        "total_revenue": [1000 + i * 50 for i in range(40)],
        "units_sold": [10 + i for i in range(40)],
    })
    return (
        OUT / "sch12_no_time.parquet",
        "SCH-12 · numeric columns exist but none have date-like names. Hard-fail.",
        df,
    )


def _build_sch13_dup_rows() -> tuple[Path, str, pd.DataFrame]:
    """SCH-13: two rows share the same (customer, product_line) tuple."""
    df = make_cube(rows=40, time_periods=12, seed=13, sparsity=0.2,
                   negatives={"count": 3})
    # Duplicate row 0's identifier tuple onto row 1 — same key, different values.
    df.loc[1, "customer"] = df.loc[0, "customer"]
    df.loc[1, "product_line"] = df.loc[0, "product_line"]
    return (
        OUT / "sch13_dup_rows.parquet",
        "SCH-13 · rows 0 and 1 share the same identifier tuple. Hard-fail names them.",
        df,
    )


def _build_sch14_string_in_measure() -> tuple[Path, str, pd.DataFrame]:
    """SCH-14: a time-named column has a non-numeric value mixed in.

    Stored as all-string column (parquet's STRING type) — most values are
    numeric-looking ("100.0") but one is "not a number". On read, the
    validator coerces and sees the failure.
    """
    df = make_cube(rows=40, time_periods=12, seed=14, sparsity=0.2,
                   negatives={"count": 3})
    col = "2021_5"
    df[col] = df[col].astype(str)
    df.loc[10, col] = "not a number"
    return (
        OUT / "sch14_string_in_measure.parquet",
        "SCH-14 · `2021_5` stored as strings, one value is non-numeric. Auto-detect drops it from measures; override path hard-fails.",
        df,
    )


def _build_sch15_dup_col_names() -> tuple[Path, str, pd.DataFrame]:
    """SCH-15: two columns share the exact same name.

    Pandas refuses duplicate column names on write, but the lower-level
    PyArrow API does support them — Parquet itself allows duplicates.
    Writing via ``pq.write_table`` directly bypasses pandas's check.
    """
    df = make_cube(rows=40, time_periods=12, seed=15, sparsity=0.2,
                   negatives={"count": 3})
    cols = list(df.columns)
    arrays = [pa.array(df[c].to_numpy()) for c in cols]
    # Rename "2021_3" to "2021_5" → two columns now both named "2021_5".
    cols[cols.index("2021_3")] = "2021_5"
    path = OUT / "sch15_dup_col_names.parquet"
    table = pa.Table.from_arrays(arrays, names=cols)
    pq.write_table(table, path)
    # Return the df with the duplicated names so the print loop still works;
    # main() detects we already wrote the file and skips a re-write.
    df_dup = df.copy()
    df_dup.columns = cols
    return (
        path,
        "SCH-15 · two columns both named `2021_5`. Hard-fail names the duplicate.",
        df_dup,
    )


def _build_broken_files(out_dir: Path) -> None:
    """Files that should fail at upload / parse — one per failure mode."""
    (out_dir / "up06_empty.parquet").write_bytes(b"")
    (out_dir / "up05a_wrong_magic.parquet").write_text("this is not a parquet file at all\n")
    (out_dir / "up05b_corrupted_body.parquet").write_bytes(b"PAR1" + b"\x00" * 256)
    happy = REPO_ROOT / "data" / "scenarios" / "happy.parquet"
    if happy.exists():
        (out_dir / "up05c_truncated.parquet").write_bytes(happy.read_bytes()[:256])
    (out_dir / "up02_csv.csv").write_text(
        "customer,product_line,2022_1,2022_2\nCust_A,X,100,200\n"
    )


if __name__ == "__main__":
    main()
