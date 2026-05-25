# Test Scenarios

Tests for the Sales Cube Cleaning Tool demo. Each test specifies an input **fixture** (a deterministic recipe for the cube under test) and the expected behavior. Assertions are written against **observable behavior** (was a cell flagged? what status was returned?) rather than internal tuning parameters (exact confidence values) — those are tested separately in a small unit-test suite.

Document structure:

- **Part A — Setup:** the data generator, canonical scenarios, named fixtures, and golden masters
- **Part B — Workflow tests:** the user journey, upload through apply
- **Part C — End-to-end flows:** full-workflow scenarios that exercise multiple stages together
- **Part D — Cross-cutting:** project management, performance, error handling
- **Part E — Deferred:** cases punted to dev time

---

# Unit test implementation status

This table tracks **whether unit tests exist** for each spec section — not whether the underlying feature works. The cleaning workflow (schema → detectors → apply → audit) is fully implemented in `app/`; this table is about unit-test coverage of that workflow. A "not yet" row means the unit test isn't written; it does not mean the feature is missing. Run everything from `backend/`:

```bash
uv run pytest -v                      # full suite (97 tests)
uv run pytest --cov=app               # with coverage
uv run pytest tests/detectors -v      # just detectors
uv run pytest -k REF-02               # find a test by spec ID (search the comments)
```

Coverage today: **126 tests pass, 96% line coverage on `app/`**.

| Section                                    | Spec rows  | Unit test status                     | Test file                                |
| ------------------------------------------ | ---------- | ------------------------------------ | ---------------------------------------- |
| A.1 Generator (GEN-01/02 + extras)         | 2          | ✓ unit tests + 22 extras             | `backend/tests/test_generator.py`        |
| A.2 Canonical scenarios on disk            | 3          | ✓ files exist (regenerable, seed 42) | `data/build_scenarios.py`                |
| A.3 Fixture library (named `F_*` fixtures) | ~45        | n/a — doc-only naming scheme         | —                                        |
| A.4 Golden masters                         | GM-01..06  | no unit test yet                     | —                                        |
| B.1 Upload & parse                         | UP-01..10  | ✓ UP-01..07, 09, 10 (auto + manual); UP-08 manual-only (no huge fixture in CI) | `backend/tests/api/test_routes.py` + UI manual |
| B.2.1 Schema role detection                | SCH-01..07 | ✓ unit tests                         | `backend/tests/test_schema.py`           |
| B.2.2 Schema hard checks                   | SCH-10, 12, 13, 14, 15 | ✓ unit tests + fixtures              | `backend/tests/test_schema.py`           |
| B.2.3 Schema observations (soft)           | SCH-11, 20..23 | ✓ unit tests + fixtures                                  | `backend/tests/test_schema.py`           |
| B.3.0 Detector math (MATH-01..03)          | 3          | indirectly via behavior unit tests — see notes | —                              |
| B.3.1 Negatives                            | NEG-01..08 | ✓ unit tests                         | `backend/tests/detectors/test_negatives.py` |
| B.3.2 Refunds                              | REF-01..10 | ✓ unit tests                         | `backend/tests/detectors/test_refunds.py` |
| B.3.3 Double bookings                      | DBL-01..11 | ✓ unit tests                         | `backend/tests/detectors/test_double_bookings.py` |
| B.3.4 Outliers                             | OUT-01..08 | ✓ unit tests                         | `backend/tests/detectors/test_outliers.py` |
| B.3.5 NaN / missing values                 | NAN-01..05 | no unit test yet                     | —                                        |
| B.4.3 Overlap merge logic                  | REV-05/06   | ✓ unit tests for the merge layer        | `backend/tests/test_detect.py`       |
| B.4 Other Review UI                        | REV-01..14 | ✓ buildable in the UI with fixture files (manual test, see §B.4) | `data/test_fixtures/*` |
| B.5.1 Apply                                | APP-01..08 | ✓ unit tests for APP-01/05/06; APP-02/03/04/07/08 need storage | `backend/tests/test_apply.py`  |
| B.5.2 Audit log                            | AUD-01..08 | ✓ unit tests for AUD-01/02/03/04/06/07/08; AUD-05 needs storage | `backend/tests/test_apply.py` |
| Part C — End-to-end flows                  | 4 flows    | no unit test yet                     | —                                        |
| Part D — Cross-cutting                     | PRJ/PERF/ERR/API | no unit test yet               | —                                        |

## Notes / deviations

| What                                | Note                                                                                       |
| ----------------------------------- | ------------------------------------------------------------------------------------------ |
| Named `F_*` fixture library (A.3)   | Doc-only naming scheme — the `F_*` names appear only in this spec, never in code. Tests build small DataFrames inline at the point of use. No shared fixture module is planned; if reuse becomes painful past ~150 tests, revisit. |
| MATH-* tests (B.3.0)                | The IQR and double-booking-magnitude assertions live inside the corresponding behavior tests today rather than a dedicated math-only file. Worth splitting out once the API layer exists and behavior tests stop touching internal score values. |
| DBL-04 fixture width                | Two `(X, 0)` spikes in the same row need enough "normal" columns that they don't pull `row_mean_positives` above the `2× X` threshold. My test uses 12 columns (2 spikes + 8 small values + 2 zeros); the spec doesn't pin a column count. |
| SCH-21 "mostly null" threshold      | Spec says ">90%". Used 95% (19/20 nulls) in the test fixture to be unambiguous; `0.9 > 0.9` is false. |
| GEN-01/02 + 22 extras               | Covers determinism plus shape, identifier uniqueness, time-format round-trips, anomaly counts, magnitude bands, error paths. |

## Sanity check (run from `backend/`)

```bash
uv run python - <<'PY'
import pandas as pd
from app.detectors.negatives import detect_negatives
from app.detectors.refunds import detect_refunds
from app.detectors.double_bookings import detect_double_bookings
from app.detectors.outliers import detect_outliers
from app.schema import infer_schema

df = pd.read_parquet("../data/scenarios/happy.parquet")
sch = infer_schema(df)
m = sch.roles.measure_columns
print(f"shape={df.shape}  ok={sch.ok}  warnings={len(sch.soft_warnings)}")
print(f"  negatives={len(detect_negatives(df, m))}")
print(f"  refunds={len(detect_refunds(df, m))}")
print(f"  double_bookings={len(detect_double_bookings(df, m))}")
print(f"  outliers={len(detect_outliers(df, m))}")
PY
```

Actual counts on `happy.parquet` (20/8/4/5 injected): **negatives=28, refunds=25, double_bookings=70, outliers=161**.

Why detector counts exceed injections:
- **Negatives 28**: 20 injected + 8 refund cells (also negative). Clean math.
- **Refunds 25**: 8 injected + ~17 injected negatives that happen to land in rows whose cumulative prior positive balance is large enough to absorb them. The detector only surfaces refunds the cleaning fix can actually unwind (see ROADMAP §6).
- **Double-bookings 70**: With sparsity=0.2 there are many natural `(positive, 0)` adjacent pairs in either direction; ~17% of them pass the `X > 2 × row_mean_positive` bar. A reminder that the detector flags *patterns*, not labels — analyst still has to judge which are real double-bookings.
- **Outliers 161**: Sparse rows often have `Q1=Q3=0`, collapsing the IQR band to zero, so every non-zero cell looks like an outlier. The default `keep_as_is` action is exactly because of this — the analyst confirms.

This is intentional and matches `ROADMAP.md §3.4`: outliers are a catch-all, not a precise detector.

---

# Part A — Setup

## A.1 Data generator

The generator emits a cube as a Pandas DataFrame and a Parquet file. Output is deterministic given the seed.

```python
make_cube(
    rows: int = 100,
    time_periods: int = 36,
    seed: int = 42,
    negatives: dict | None = None,        # {"count": int, "magnitude": "small"|"medium"|"large", "positions": [(row, col), ...]}
    refunds: dict | None = None,          # {"count": int, "style": "round"|"mom_drop"|"mixed", "positions": [...]}
    double_bookings: dict | None = None,  # {"count": int, "positions": [...]}
    outliers: dict | None = None,         # {"count": int, "positions": [...]}
    sparsity: float = 0.3,                # fraction of zero cells outside anomalies
    noise: float = 0.0,                   # random perturbation on non-anomaly cells
    time_format: str = "YYYY_M",          # "YYYY_M" | "YYYY-MM" | "Mon-YY" | "YYYYQn"
    id_columns: list[str] = ["customer", "product_line"],
) -> pd.DataFrame
```

The `positions` overrides let fixtures pin anomalies at specific cells for deterministic per-cell assertions.

**Determinism test:**

| ID    | Description                                              | Expected                                  |
| ----- | -------------------------------------------------------- | ----------------------------------------- |
| GEN-01 | `make_cube(seed=42)` called twice                       | Two outputs are bitwise identical          |
| GEN-02 | Same parameters, different seeds                        | Outputs differ but each is reproducible   |

**Running the generator tests:**

```bash
# Full generator suite (24 tests), from backend/
cd backend && uv run pytest tests/test_generator.py -v

# Single test by name
cd backend && uv run pytest tests/test_generator.py -k determinism -v

# REPL — poke at make_cube directly. Run from repo root.
uv run --project backend python -c "
from data.generator import make_cube
df = make_cube(rows=20, time_periods=12, seed=7,
               negatives={'count': 3, 'magnitude': 'medium'})
print(df.head()); print(df.shape)
"
```

`--project backend` is required for the REPL form because deps (pandas, numpy) live in `backend/pyproject.toml` while `data/` sits at the repo root.

## A.2 Canonical scenarios

Committed to `data/scenarios/` for stage demos. Regenerate with:

```bash
uv run --project backend python data/build_scenarios.py
```

Deterministic (seed 42) — re-running overwrites with identical content.

| File             | Rows | Negatives | Refunds | Double-bookings | Outliers | Sparsity | Notes                                |
| ---------------- | ---- | --------- | ------- | --------------- | -------- | -------- | ------------------------------------ |
| `happy.parquet`  | 100  | 20        | 8       | 4               | 5        | 0.2      | Small, easy to walk through on stage |
| `dirty.parquet`  | 5k   | 250       | 80      | 40              | 120      | 0.4      | Realistic mid-size; noise enabled    |
| `stress.parquet` | 500k | 2,000     | 500     | 200             | 800      | 0.5      | Demo's upper-bound size              |

## A.3 Fixture library

Named fixtures referenced throughout the test tables below. Each fixture is one `make_cube(...)` call (or, for things the generator can't synthesize cleanly, a brief description of how to build the file manually).

### Cleanliness baselines

| Fixture           | Definition                                                                          |
| ----------------- | ----------------------------------------------------------------------------------- |
| `F_clean`         | `make_cube(rows=100, seed=1)` — no anomalies                                        |
| `F_clean_5k`      | `make_cube(rows=5000, seed=1)` — no anomalies, mid-size                             |
| `F_clean_500k`    | `make_cube(rows=500_000, seed=1)` — no anomalies, demo upper bound                  |

### Negative-value fixtures

| Fixture                | Definition                                                                                                  |
| ---------------------- | ----------------------------------------------------------------------------------------------------------- |
| `F_neg_one_cell`       | `make_cube(seed=11, negatives={"count": 1, "positions": [(5, "2022_5")]})`                                  |
| `F_neg_many`           | `make_cube(seed=12, negatives={"count": 1000})`                                                             |
| `F_neg_all`            | Cube where every measure cell is `-1`. Built manually.                                                      |
| `F_neg_in_id_col`      | Cube where `customer` column has a `"-1"` value as a string. Built manually.                                |
| `F_neg_signed_zero`    | Cube with `-0.0` cells. Built manually using `numpy.float64(-0.0)`.                                         |
| `F_neg_tiny`           | Cube with `-0.000001` cells. Built manually.                                                                |

### Refund fixtures

Each refund fixture pins specific cells so assertions can name them.

| Fixture                | Definition                                                                                                  |
| ---------------------- | ----------------------------------------------------------------------------------------------------------- |
| `F_ref_none`             | `F_clean`                                                                                                 |
| `F_ref_paired`           | Cube with `2022_7 = 200`, `2022_8 = -200` in one row, other cells positive                                |
| `F_ref_partial`          | Cube with `2022_7 = 1,000`, `2022_8 = -50` — partial refund (small negative after large positive)         |
| `F_ref_tiny_after_pos`   | Cube with `2022_7 = 100`, `2022_8 = -1` — tiny refund still flags as candidate                            |
| `F_ref_no_prior_positive`| Cube with `2022_7 = 0`, `2022_8 = -200` — prior period not positive                                       |
| `F_ref_first_column`     | Cube with the first time column holding a negative — no prior period to compare                           |
| `F_ref_prior_negative`   | Cube with `2022_7 = -50`, `2022_8 = -200` — back-to-back negatives                                        |
| `F_ref_multi_per_row`    | One row with two paired reversals at different periods (e.g., `200, -200, 300, -300`)                     |
| `F_ref_year_boundary`    | `2022_12 = 12,000`, `2023_1 = -10,000` — paired reversal across year boundary                             |
| `F_ref_non_adjacent`     | `2022_7 = 10,000`, `2022_8 = 0`, `2022_9 = -10,000` — refund posted one period later than the sale        |

### Double-booking fixtures

| Fixture                | Definition                                                                                                  |
| ---------------------- | ----------------------------------------------------------------------------------------------------------- |
| `F_dbl_none`           | `F_clean`                                                                                                   |
| `F_dbl_clear`          | `2022_5 = 10,000`, `2022_6 = 0`, rest of row averages 2,000                                                  |
| `F_dbl_close_to_mean`  | `2022_5 = 2,100`, `2022_6 = 0`, row mean 2,000 — fails magnitude check                                      |
| `F_dbl_multi_in_row`   | Same row has two `(X, 0)` patterns at different periods                                                     |
| `F_dbl_at_end`         | Spike in the last time column (no neighbor to compare)                                                      |
| `F_dbl_at_start`       | Spike in the first time column, followed by 0                                                               |
| `F_dbl_triple_zero`    | `(X, 0, 0)` pattern                                                                                         |
| `F_dbl_neighbor_small` | `(X, 1)` where the neighbor is non-zero but tiny                                                            |
| `F_dbl_odd_amount`     | `(101, 0)`                                                                                                  |
| `F_dbl_negative_spike` | `(-1000, 0)` — pattern should not match (X must be > 0)                                                     |

### Outlier fixtures

| Fixture                | Definition                                                                                                  |
| ---------------------- | ----------------------------------------------------------------------------------------------------------- |
| `F_out_uniform_row`    | Row with `[100, 100, 100, ..., 100]` — IQR = 0                                                              |
| `F_out_one_spike`      | Row with one extreme value (e.g., `[100, 100, ..., 100, 10000]`)                                            |
| `F_out_two_spikes`     | Row with two extreme values, consecutive                                                                    |
| `F_out_all_zero`       | Row with all zeros                                                                                          |
| `F_out_all_negative`   | Row with all negative values, varied                                                                        |
| `F_out_single_col`     | Cube with one time column only — IQR undefined per row                                                      |
| `F_out_zero_outlier`   | Row with `[0, 0, 0, 0, 100]` — `100` flagged as outlier (Q1 = Q3 = 0, IQR = 0)                              |
| `F_out_zero_below`     | Row with `[1000, 1000, ..., 1000, 0]` — `0` flagged below band                                              |

### Schema fixtures

| Fixture                | Definition                                                                                                  |
| ---------------------- | ----------------------------------------------------------------------------------------------------------- |
| `F_sch_std`            | `F_clean` — standard cube, no warnings                                                                      |
| `F_sch_yyyy_mm`        | `make_cube(seed=20, time_format="YYYY-MM")`                                                                 |
| `F_sch_mon_yy`         | `make_cube(seed=21, time_format="Mon-YY")`                                                                  |
| `F_sch_quarterly`      | `make_cube(seed=22, time_format="YYYYQn", time_periods=12)`                                                 |
| `F_sch_mixed_formats`  | Manual: standard cube with two columns renamed to a different format                                        |
| `F_sch_no_id`          | Manual: drop `customer` and `product_line` from a standard cube                                             |
| `F_sch_no_measure`     | Manual: only ID columns, no measures                                                                        |
| `F_sch_no_time`        | Manual: replace time-column names with non-temporal strings                                                 |
| `F_sch_dup_rows`       | Manual: duplicate two `(customer, product_line)` rows                                                       |
| `F_sch_string_in_meas` | Manual: set one cell of a measure column to `"abc"`                                                         |
| `F_sch_dup_col_names`  | Manual: rename two time columns to the same name                                                            |
| `F_sch_time_gap`       | Manual: drop `2022_5` from a standard cube                                                                  |
| `F_sch_mostly_null`    | Manual: blank out 95% of one measure column                                                                 |
| `F_sch_all_zero_row`   | Manual: set one row's measures to zero                                                                      |
| `F_sch_mixed_dtypes`   | Manual: insert a string value into one cell of a numeric column                                             |

### Overlap fixtures (used in cross-detector tests)

| Fixture                | Definition                                                                                                  |
| ---------------------- | ----------------------------------------------------------------------------------------------------------- |
| `F_overlap_neg_ref`    | Cell `-10,000` that is both a negative and a refund (same fix)                                              |
| `F_overlap_neg_out`    | Cell `-50,000` that is a negative AND a row-level outlier (conflicting fixes)                               |
| `F_overlap_ref_out`    | Cell `-10,000` that is a refund AND a row-level outlier (conflicting fixes)                                 |
| `F_overlap_dbl_out`    | A `(X, 0)` pair where X is also a row-level outlier                                                         |
| `F_overlap_triple`     | A cell flagged by negatives + refunds + outliers                                                            |

## A.4 Golden masters

For each canonical scenario, commit the input file alongside expected outputs. A regression test compares live outputs to the goldens.

```
data/scenarios/
├── happy.parquet
├── happy.detections.json    # expected detections per detector
├── happy.cleaned.parquet    # expected cleaned cube after "apply all suggested"
├── happy.audit.json         # expected audit log
├── dirty.parquet
├── dirty.detections.json
├── dirty.cleaned.parquet
├── dirty.audit.json
├── stress.parquet
└── stress.detections.json   # detections only (no apply at this scale during CI)
```

| ID    | Description                                                            | Expected                                                |
| ----- | ---------------------------------------------------------------------- | ------------------------------------------------------- |
| GM-01 | Run all four detectors on `happy.parquet`                              | Detections match `happy.detections.json` exactly        |
| GM-02 | Apply all suggested fixes on `happy.parquet`                           | Output matches `happy.cleaned.parquet` exactly          |
| GM-03 | Audit log from GM-02                                                   | Matches `happy.audit.json` (modulo `applied_at` timestamps and UUIDs) |
| GM-04 | Same as GM-01 for `dirty.parquet`                                      | Matches `dirty.detections.json`                         |
| GM-05 | Same as GM-02 for `dirty.parquet`                                      | Matches `dirty.cleaned.parquet`                         |
| GM-06 | Detection only on `stress.parquet`                                     | Matches `stress.detections.json` (apply skipped)        |

Golden masters are regenerated when detector behavior intentionally changes; the regeneration is a deliberate commit, never silent.

---

# Part B — Workflow tests

## B.1 Upload & parse

The upload flow is two-step: browser POSTs to `/projects/{slug}/files` to get a presigned PUT URL, PUTs the file directly to S3, then POSTs to `/parse`. The manifest entry is only written when `/parse` succeeds — failed uploads (CORS, network, corrupt) leave no orphan. Where validation happens is called out per row.

| ID    | Description                            | Fixture / Input                                  | Expected                                                       | Validates at | Notes |
| ----- | -------------------------------------- | ------------------------------------------------ | -------------------------------------------------------------- | ------------ | ----- |
| UP-01 | Valid Parquet                          | `data/test_fixtures/up01_clean.parquet`          | Upload + parse succeed; file appears in manifest as `schema_pending` | full path | Progress bar shows in dropzone during PUT |
| UP-02 | CSV file uploaded                      | `data/test_fixtures/up02_csv.csv`                | "Only Parquet files are supported in this demo." (red banner)  | frontend (extension) | Backend also rejects as defense-in-depth (`test_upload_non_parquet_rejected`) |
| UP-03 | Excel file uploaded                    | Any `.xlsx`                                      | Same as UP-02                                                  | frontend (extension) | Same code path as UP-02 |
| UP-04 | PDF / image upload                     | Any `.pdf` / `.png`                              | Same as UP-02                                                  | frontend (extension) | Same code path as UP-02 |
| UP-05 | Corrupted Parquet                      | `up05a_wrong_magic.parquet`, `up05b_corrupted_body.parquet`, `up05c_truncated.parquet` | "Unable to parse Parquet — the file is corrupted or not a valid Parquet." | mixed | (a) wrong magic bytes caught by frontend; (b) valid `PAR1` header but junk body caught by backend in `/parse` (`test_parse_corrupted_parquet_returns_400`); (c) truncated real Parquet also caught by backend. Bad S3 object is auto-deleted. |
| UP-06 | Empty file                             | `data/test_fixtures/up06_empty.parquet` (0 bytes) | "File is empty."                                              | frontend (`file.size === 0`) | Caught before any network call |
| UP-07 | Parquet with schema, zero rows         | Built in `test_parse_zero_row_parquet_returns_400` | "File has no data rows."                                       | backend (`/parse`) | Frontend can't tell row count without parsing; backend rejects after read |
| UP-08 | File exceeds configured size limit     | Synthetic >2 GB                                  | "File exceeds the 2 GB limit (got N MB)."                      | frontend (size check) | Frontend cap of 2 GB enforced in `uploadFile`; not exercised in CI (no easy huge fixture) |
| UP-09 | Re-upload same filename                | Upload `up01_clean.parquet` twice into same project | Two distinct `file_id`s; second appears in list as `up01_clean (2).parquet` | n/a | Display-only suffix; underlying filenames stay as uploaded |
| UP-10 | Network drop mid-upload                | DevTools → Network → Offline mid-PUT             | XHR `onerror` fires → "Network error during upload." banner; no manifest entry; no S3 object | XHR error handler | Not resumable (no S3 multipart). Manifest stays clean because writes are deferred to `/parse`. |

## B.2 Schema validation

### B.2.1 Role detection

| ID     | Description                            | Fixture                                              | Expected                                                          |
| ------ | -------------------------------------- | ---------------------------------------------------- | ----------------------------------------------------------------- |
| SCH-01 | Standard cube                          | `data/test_fixtures/up01_clean.parquet`              | IDs / time / measures detected; no observations                   |
| SCH-02 | `YYYY-MM` time format                  | `data/test_fixtures/sch02_yyyy_mm.parquet`           | Time columns detected, format reads `YYYY-MM`                     |
| SCH-03 | `Mon-YY` time format                   | `data/test_fixtures/sch03_mon_yy.parquet`            | Time columns detected, format reads `Mon-YY`                      |
| SCH-04 | Quarterly time format                  | `data/test_fixtures/sch04_quarterly.parquet`         | Time columns detected, format reads `YYYYQn`                      |
| SCH-05 | Mixed formats in one file              | `data/test_fixtures/sch05_mixed_formats.parquet`     | Observation: "Time columns mix multiple formats"                  |
| SCH-06 | Override valid                         | any standard cube + **Adjust column roles** modal    | User remaps a column to a different role, Save accepts, schema panel + Schema observations refresh |
| SCH-07 | Override invalid (numeric coerce fail) | any standard cube + **Adjust column roles** modal    | User marks `customer` as Measure → red banner in modal: "contains non-numeric data" |

### B.2.2 Hard checks (block detection)

| ID     | Condition                              | Fixture                            | Expected                                                  |
| ------ | -------------------------------------- | ---------------------------------- | --------------------------------------------------------- |
| SCH-10 | No identifier columns                  | `data/test_fixtures/sch10_no_id.parquet` | Hard fail: "No identifier columns detected"               |
| SCH-12 | No time columns                        | `data/test_fixtures/sch12_no_time.parquet` | Hard fail: "No time columns detected"                     |
| SCH-13 | Duplicate identifier rows              | `data/test_fixtures/sch13_dup_rows.parquet` | Hard fail naming the offending tuples and count           |
| SCH-14 | Non-numeric in measure column          | `data/test_fixtures/sch14_string_in_measure.parquet` | Auto-detect drops the column from measures; override path hard-fails with column name + sample bad value |
| SCH-15 | Duplicate column names                 | `data/test_fixtures/sch15_dup_col_names.parquet` | Hard fail naming the duplicate                            |

### B.2.3 Soft warnings → Schema observations (surface, don't block)

| ID     | Condition                              | Fixture                            | Expected                                                              |
| ------ | -------------------------------------- | ---------------------------------- | --------------------------------------------------------------------- |
| SCH-11 | No numeric measure columns (time-named columns are all strings) | `data/test_fixtures/sch11_no_measure.parquet` | Observation: "No sales numbers found in this file." File walks through Detect / Apply; detection finds nothing. |
| SCH-20 | Gap in time sequence                   | `data/test_fixtures/sch20_time_gap.parquet` | Observation names the missing period(s) in human format (e.g. `2022_5`) |
| SCH-21 | Column mostly null                     | `data/test_fixtures/sch21_mostly_null.parquet` | Observation names the column + null %                                 |
| SCH-22 | Row all-zero                           | `data/test_fixtures/sch22_all_zero_row.parquet` | Observation with affected row count                                   |
| SCH-23 | Time columns out of chronological order | `data/test_fixtures/sch23_out_of_order.parquet` | Observation: "Your time periods weren't in chronological order. We sorted them automatically." Backend auto-sorts before detection; source file unchanged. |

## B.3 Detection

### B.3.0 Detector math (unit tests, decoupled from behavior)

These test the score / threshold functions in isolation, with hand-crafted numeric inputs. They're allowed to assert specific score values because they're the tests that catch math regressions.

| ID      | Description                                                                  | Expected                                                                            |
| ------- | ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| MATH-01 | Double-booking magnitude: `X > 2 × row_mean_positive`                        | Pair `(X, 0)` matches                                                               |
| MATH-02 | IQR rule on `[10, 12, 12, 12, 100]`                                          | `100` lies outside `[Q1 − 1.5·IQR, Q3 + 1.5·IQR]`; flagged                          |
| MATH-03 | IQR rule on `[100, 100, 100, 100, 100]`                                      | IQR = 0; no cells flagged                                                           |

Tests B.3.1 onward assert **behavior** only — whether a cell is flagged — without coupling to specific confidence numbers.

### B.3.1 Negatives — behavior

| ID     | Description                            | Tested in                                                                  | Expected                                                  |
| ------ | -------------------------------------- | -------------------------------------------------------------------------- | --------------------------------------------------------- |
| NEG-01 | No negative cells                      | `tests/detectors/test_negatives.py::test_no_negatives_no_detections` + `data/test_fixtures/up01_clean.parquet` | 0 detections                                              |
| NEG-02 | Single negative cell                   | `test_one_negative_one_detection`                                          | 1 detection at the expected `(row, col)`                  |
| NEG-03 | Many negative cells                    | `test_many_negatives_correct_count` + `data/test_fixtures/neg03_many.parquet` | Detection count == number of injected negatives           |
| NEG-04 | All cells negative                     | `test_all_measure_negative`                                                | Every measure cell flagged                                |
| NEG-05 | Negative value in ID column            | `test_negative_in_identifier_column_ignored`                               | No detection (negatives only run on measure columns)      |
| NEG-06 | Cell exactly `0.0`                     | `test_zero_not_flagged`                                                    | Not detected                                              |
| NEG-07 | Signed zero `-0.0`                     | `test_negative_zero_not_flagged`                                           | Not detected                                              |
| NEG-08 | Tiny negative `-0.000001`              | `test_tiny_negative_flagged`                                               | Detected                                                  |

### B.3.2 Refunds — behavior

| ID     | Description                            | Tested in                                                                  | Expected                                                  |
| ------ | -------------------------------------- | -------------------------------------------------------------------------- | --------------------------------------------------------- |
| REF-01 | No negative cells                      | `tests/detectors/test_refunds.py::test_no_negatives_no_detections`         | 0 detections                                              |
| REF-02 | Paired reversal `(200 → -200)`         | `test_paired_reversal_flagged`                                             | Flagged — prior balance exactly matches the reversal      |
| REF-03 | Refund magnitude exceeds prior balance `(550, -8,900)` | `test_refund_exceeding_balance_not_flagged`            | Not flagged — cumulative prior positives less than `\|neg\|` |
| REF-04 | Negative in first time column          | `test_negative_in_first_column_not_flagged`                                | Not flagged — no prior columns to build balance           |
| REF-05 | Prior negative, earlier positive covers it | `test_negative_with_earlier_positive_balance_flagged`                  | Flagged — balance comes from non-adjacent positive        |
| REF-06 | Partial refund (`+1000` → `-50`)       | `test_partial_refund_flagged`                                              | Flagged                                                   |
| REF-07 | Multiple paired reversals in same row  | `test_multiple_reversals_in_same_row`                                      | All flagged                                               |
| REF-08 | Tiny negative after positive (`-1`)    | `test_tiny_negative_after_positive_flagged`                                | Flagged                                                   |
| REF-09 | Refund against non-adjacent prior sale `(10k, 0, -8.9k)` | `test_refund_against_non_adjacent_prior_sale_flagged`    | Flagged — cumulative balance covers the reversal          |
| REF-10 | Cell also flagged by negatives         | `test_overlap_with_negatives_detector`                                     | Same cell flagged by both detectors, same `set_to_zero` fix |

### B.3.3 Double bookings — behavior

**Pattern is bidirectional.** A spike adjacent to a zero on **either** side counts — both `(X, 0)` and `(0, X)` shapes. The rows below use `(X, 0)` in descriptions for brevity but every check applies symmetrically: the same rules and outcomes hold if you mirror the spike to the right of the zero. Apply prefers splitting forward (toward the next column) when both neighbors qualify; falls back to splitting backward when only the previous column is the zero.

| ID     | Description                            | Tested in                                                                  | Expected                                                  |
| ------ | -------------------------------------- | -------------------------------------------------------------------------- | --------------------------------------------------------- |
| DBL-01 | No double-booking patterns             | `tests/detectors/test_double_bookings.py::test_no_double_bookings`         | 0 detections                                              |
| DBL-02 | Clear spike + zero                     | `test_clear_spike_zero_pattern`                                            | Flagged; default `split_evenly` toward the zero neighbor  |
| DBL-03 | Spike close to row mean                | `test_close_to_mean_spike_not_detected`                                    | Not flagged                                               |
| DBL-04 | Multiple spike+zero pairs in same row  | `test_multiple_patterns_same_row` + `data/test_fixtures/dbl04_many_pairs.parquet` | All flagged                                              |
| DBL-05 | Spike at last column with zero left-neighbor | `test_spike_at_last_column_with_zero_prev_flagged` + `test_spike_at_last_column_no_zero_neighbor_not_flagged` | Flagged when the prior period is 0; not flagged when no neighbor is 0 |
| DBL-06 | Spike at first column with zero right-neighbor | `test_spike_at_first_column`                                               | Flagged                                                   |
| DBL-07 | `(X, 0, 0)` triple                     | `test_spike_followed_by_two_zeros`                                         | Flagged on the spike only. The trailing zero isn't a spike itself. |
| DBL-08 | Small non-zero neighbor                | `test_neighbor_must_be_exact_zero`                                         | Not flagged (neighbor must be exactly 0)                 |
| DBL-09 | Odd amount `(101, 0)`                  | `test_odd_amount_detected` + apply path: `tests/test_apply.py::test_split_evenly_odd_amount_favors_earlier` | Flagged; on apply, split `{51, 50}` — spike side keeps the larger half |
| DBL-10 | Negative spike                         | `test_negative_spike_not_detected`                                         | Not flagged (X must be positive)                          |
| DBL-11 | All-zero row                           | `test_all_zero_row`                                                        | Not flagged (no spike present)                            |

### B.3.4 Outliers — behavior

| ID     | Description                            | Tested in                                                                  | Expected                                                  |
| ------ | -------------------------------------- | -------------------------------------------------------------------------- | --------------------------------------------------------- |
| OUT-01 | Uniform row                            | `tests/detectors/test_outliers.py::test_all_identical_non_zero_no_outliers` | No outliers (IQR = 0)                                     |
| OUT-02 | One extreme spike                      | `test_single_positive_spike_flagged` + `data/test_fixtures/out_storm.parquet` | Spike flagged                                             |
| OUT-03 | Two consecutive spikes                 | `test_two_extreme_spikes_both_flagged`                                     | Both flagged                                              |
| OUT-04 | All-zero row                           | `test_all_zero_row`                                                        | No outliers                                               |
| OUT-05 | All-negative row                       | `test_all_negative_row_can_have_outliers`                                  | IQR computed normally; outliers possible                  |
| OUT-06 | Single time column                     | `test_single_column_no_detections`                                         | 0 detections                                              |
| OUT-07 | Zero-band row with one positive        | `test_iqr_zero_above_band`                                                 | The positive value flagged                                |
| OUT-08 | Positive band with one zero            | `test_iqr_zero_below_band`                                                 | The zero flagged                                          |

### B.3.5 NaN / missing values

| ID     | Description                            | Input                    | Expected                                                  |
| ------ | -------------------------------------- | ------------------------ | --------------------------------------------------------- |
| NAN-01 | Measure cell is NaN                    | Manually inject NaN      | Not flagged by negatives (NaN < 0 is False)               |
| NAN-02 | Measure cell is NaN                    | Same                     | Not flagged by refunds (signal evaluations skip NaN)      |
| NAN-03 | Measure cell is NaN                    | Same                     | Not flagged by double-bookings (cell is not zero, not X)  |
| NAN-04 | Row has all NaN measures               | Manually inject row       | IQR not computed; no outliers from that row; warning      |
| NAN-05 | Row has some NaN, some non-NaN         | Manually inject mix       | IQR computed over non-NaN values                          |

## B.4 Review UI

All B.4 rows are **manual** — drag the listed Parquet into the dropzone, walk through Schema → Detect & review, and verify against "Expected". The fast happy path: `demo_mixed_anomalies.parquet` exercises REV-01/02/04/05/06/09/10/12/13 in one upload.

### B.4.1 Empty and large states

| ID     | Description                            | Drag this file                                              | Expected                                                              |
| ------ | -------------------------------------- | ----------------------------------------------------------- | --------------------------------------------------------------------- |
| REV-01 | Zero detections across all detectors   | `data/test_fixtures/rev01_no_anomalies.parquet`             | "No anomalies" empty state; user can finalize as-is                   |
| REV-02 | One detection per detector             | `data/test_fixtures/demo_mixed_anomalies.parquet`           | All four detector chips visible in the left rail with counts          |
| REV-03 | Many detections, virtualized scrolling | `data/scenarios/dirty.parquet` (canonical) or `data/test_fixtures/out_storm.parquet` | Smooth scroll through hundreds of rows                  |

### B.4.2 Filter and sort

| ID     | Description                            | Drag this file                                              | Expected                                                              |
| ------ | -------------------------------------- | ----------------------------------------------------------- | --------------------------------------------------------------------- |
| REV-04 | Filter to one detector type            | `data/test_fixtures/demo_mixed_anomalies.parquet`           | Clicking a left-rail tab filters preview rows to those containing that detector's cells (both Before/After panes); other cells in those rows are still visible for context. Switching to "All" restores the full row set. |

### B.4.3 Cell interaction

A flagged cell renders one colored **dot** per entry in `flagged_by` in its top-right corner. The bottom **staged-changes** bar lists every staged cell with one pill per detector that flagged it; clicking a pill switches both the attribution and the fix.

| ID     | Description                            | Drag this file                                              | Expected                                                              |
| ------ | -------------------------------------- | ----------------------------------------------------------- | --------------------------------------------------------------------- |
| REV-05 | Neg + Refund overlap (same fix)        | `data/test_fixtures/demo_mixed_anomalies.parquet`           | Two dots. Click on "All" stages `set_to_zero`; refund cascade absorbs the magnitude from prior positive periods (FIFO, most recent first). |
| REV-06 | Neg + Outlier overlap (conflict)       | `data/test_fixtures/demo_mixed_anomalies.parquet`           | Two dots. Default click stages `set_to_zero` (negative wins priority over outlier's `keep_as_is`). Bar shows two pills; clicking "Keep as is" switches the fix and AFTER reverts to the original value with a `⊙` marker. |
| REV-07 | Double-booking cell, no overlap        | `data/test_fixtures/dbl04_many_pairs.parquet`               | One blue dot. Click stages `split_evenly`; AFTER halves the spike across the `(X, 0)` pair (odd amounts: earlier cell keeps the larger half). |
| REV-08 | Outlier-only cell                      | `data/test_fixtures/out_storm.parquet`                      | One purple dot. Click stages `keep_as_is`; AFTER value unchanged, `⊙` marker shown.                |

### B.4.4 Bulk operations

| ID     | Description                            | Drag this file                                              | Expected                                                              |
| ------ | -------------------------------------- | ----------------------------------------------------------- | --------------------------------------------------------------------- |
| REV-09 | "Stage all" with a tab filter          | `data/test_fixtures/demo_mixed_anomalies.parquet`           | Pick a detector tab → title-bar "Stage all" stages only that tab's detections. Counter reads `N of N staged`. |
| REV-10 | "Stage none" inside a tab              | any file with staged selections                             | Title-bar "Stage none" unstages only the current tab's detections; selections in other tabs untouched. |
| REV-11 | "Clear staged" (left rail)             | any file with staged selections                             | Left-rail footer button wipes every selection across all tabs; disabled when nothing is staged. |

### B.4.5 Dual-pane behavior

| ID     | Description                            | Drag this file                                              | Expected                                                              |
| ------ | -------------------------------------- | ----------------------------------------------------------- | --------------------------------------------------------------------- |
| REV-12 | Lockstep scroll                        | `data/test_fixtures/demo_mixed_anomalies.parquet`           | Scrolling either pane (vertical or horizontal) tracks the other in sync. |
| REV-13 | Click anomaly in either pane           | `data/test_fixtures/demo_mixed_anomalies.parquet`           | Clicking a flagged cell in BEFORE or AFTER stages the same fix. AFTER updates the cell and side-effect cells (refund cascade, split partner); BEFORE shows the original value struck through. |
| REV-14 | Selections persist across stages       | any file with staged cells                                  | Stage 2–3 cells, switch to Schema via the stepper, return to Review — staged cells still ringed green and listed in the bar. |

## B.5 Apply & audit log

### B.5.1 Apply

| ID     | Description                            | Fixture / Setup                              | Expected                                                              |
| ------ | -------------------------------------- | -------------------------------------------- | --------------------------------------------------------------------- |
| APP-01 | 0 staged                               | `F_neg_one_cell`, nothing selected           | Apply disabled, or confirmation "No changes — finalize as-is?"        |
| APP-02 | 50 staged                              | `F_neg_many`, 50 selected                    | Atomic write of cleaned `.parquet` + audit `.json` to S3              |
| APP-03 | S3 write fails midway                  | Inject failure on second write call          | Neither file committed; retry surfaced                                |
| APP-04 | Apply twice on same file               | Apply, then re-call apply                    | Second call returns "File already cleaned"                            |
| APP-05 | Overlapping cells applied              | `F_overlap_neg_ref`, both staged             | Refund wins priority over negative. Refund cell zeroed; walk backward absorbing from positive prior periods until the magnitude is exhausted. One audit entry per absorbed cell, all attributed to `refund`. |
| APP-06 | Outlier-only rows applied              | `F_out_one_spike`, flag staged               | Audit entry with `value_before == value_after`, `flagged: true`       |
| APP-07 | Apply on 500k file                     | `F_clean_500k` with anomalies injected       | Completes within performance budget                                   |
| APP-08 | Original file deleted before apply     | Apply after manually deleting `original.parquet` | 400 error: "Original file missing"                                |

### B.5.2 Audit log

| ID     | Description                            | Setup                                        | Expected                                                                          |
| ------ | -------------------------------------- | -------------------------------------------- | --------------------------------------------------------------------------------- |
| AUD-01 | 10 changes across 3 detector types     | Apply on `F_overlap_triple` setup            | Audit log has 10 entries; summary header counts match per type                    |
| AUD-02 | Unique `change_id`s                    | Any apply                                    | All UUIDs distinct                                                                |
| AUD-03 | Single `applied_at` per apply          | Any apply                                    | Same timestamp across all entries from that apply                                 |
| AUD-04 | Valid JSON                             | Any apply                                    | Parses cleanly; no trailing commas; all fields present                            |
| AUD-05 | Presigned URL works                    | Any apply                                    | URL fetches the audit log                                                         |
| AUD-06 | Outlier flag entries                   | `F_out_one_spike` apply                      | `value_before == value_after`, `flagged: true`                                    |
| AUD-07 | Double-booking split entries           | `F_dbl_clear` apply with split               | Two entries: first `before == X`, `after == X/2`; second `before == 0`, `after == X/2` |
| AUD-08 | Double-booking remove entries          | `F_dbl_clear` apply with remove              | One entry: `before == X`, `after == 0`                                            |

---

# Part C — End-to-end flows

These exercise the full workflow on realistic fixtures. Each one is a sequenced script of steps with expected outcomes per step.

## C.1 Happy-path workflow

Walks `happy.parquet` from upload through download, asserting against the committed golden masters.

| Step | Action                                          | Expected                                                                                              |
| ---- | ----------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| 1    | Upload `happy.parquet` to project "Test"        | File appears in project list with status `uploaded`                                                   |
| 2    | Open schema validation                          | Auto-detected roles match expected for `happy.parquet`; zero soft warnings                            |
| 3    | Confirm schema, trigger detection               | Status → `detected`; all four detector counts populated                                               |
| 4    | Open review                                     | Before/After view loads; all detected cells highlighted; 0 staged                                     |
| 5    | "Select all visible" with no filter             | All anomaly cells staged; counter shows total                                                         |
| 6    | Click Apply                                     | Cleaned parquet + audit JSON written; status → `cleaned`                                              |
| 7    | Compare outputs to goldens                      | Detections match `happy.detections.json`; cleaned cube matches `happy.cleaned.parquet`; audit log matches `happy.audit.json` modulo timestamps and UUIDs |
| 8    | Download cleaned + audit                        | Presigned URLs return the exact files in S3                                                           |

## C.2 Multi-detector overlap resolution

Exercises overlap UX on a single cube where one cell is flagged by every detector.

| Step | Action                                          | Expected                                                                                              |
| ---- | ----------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| 1    | Upload `F_overlap_triple`                       | Uploaded; schema confirmed                                                                            |
| 2    | Run detection                                   | Three chips on the overlap cell                                                                       |
| 3    | Click the cell                                  | Staged-changes bar shows pills for each detector — `Set to 0`, `Apply refund`, `Keep as is`           |
| 4    | Pick `Set to 0`                                 | After-pane cell updates to 0; entry attributed to whichever detector's pill was chosen                |
| 5    | Apply                                           | One audit entry, attributed to the chosen detector                                                    |

## C.3 Schema override and recover

Exercises the path where auto-detect picks the wrong role and the user corrects it.

| Step | Action                                          | Expected                                                                                              |
| ---- | ----------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| 1    | Upload a cube where `customer_id` is numeric    | Auto-detect tags `customer_id` as measure                                                             |
| 2    | Open "Adjust column roles", flip to identifier  | Override accepted                                                                                     |
| 3    | Re-validate                                     | Passes; correct measure / identifier split                                                            |
| 4    | Run detection                                   | Counts non-zero; no warnings about identifier confusion                                               |

## C.4 Apply failure and retry

| Step | Action                                          | Expected                                                                                              |
| ---- | ----------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| 1    | Upload `happy.parquet`, stage all anomalies     | OK                                                                                                    |
| 2    | Inject S3 write failure on `audit.json` write   | Apply request returns 500                                                                             |
| 3    | Check S3                                        | Neither `cleaned.parquet` nor `audit.json` exists for this file                                       |
| 4    | Remove the failure injection, retry             | Apply succeeds; outputs match goldens                                                                 |

## C.5 Re-upload and re-detect

| Step | Action                                          | Expected                                                                                              |
| ---- | ----------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| 1    | Upload `happy.parquet`, complete the workflow   | Cleaned file produced                                                                                 |
| 2    | Upload a corrected version with same filename   | New `file_id`; manifest notes lineage to the previous one                                             |
| 3    | Run the workflow on the new file                | Independent detection and apply; previous cleaned file untouched                                      |

---

# Part D — Cross-cutting concerns

## D.1 Project & file management

| ID     | Description                                                                  | Fixture / Setup                                  | Expected                                                              |
| ------ | ---------------------------------------------------------------------------- | ------------------------------------------------ | --------------------------------------------------------------------- |
| PRJ-01 | New project, no files                                                        | Fresh project "P1"                               | Project page shows empty state                                        |
| PRJ-02 | Project with files in mixed statuses                                         | Upload 3 files at different stages               | All listed with status badges                                         |
| PRJ-03 | Switch project mid-workflow with unstaged selections                         | Stage some, click "Switch project"               | Confirmation prompt before navigating away                            |
| PRJ-04 | Resume via recent-projects list                                              | Re-enter project from gate                       | Manifest loaded; file statuses preserved                              |
| PRJ-05 | Project name collision across browsers                                       | Same name in two browsers                        | Manifest loadable by slug; both users share state                     |
| PRJ-06 | Project name with non-ASCII                                                  | Name = "Acme — Q1 — España"                      | Slug ASCII-folded; displayed name preserved                           |

## D.2 Performance

Budgets are stated for a **shared-cpu-1x Fly machine with 2GB RAM** (a single configuration so the numbers are meaningful). Different machines need different budgets.

| ID      | Description                                                                  | Fixture          | Expected                                            |
| ------- | ---------------------------------------------------------------------------- | ---------------- | --------------------------------------------------- |
| PERF-01 | Upload + parse + schema validation, 500k rows                                | `F_clean_500k`   | < 10s end-to-end                                    |
| PERF-02 | Detection (all four detectors), 500k rows                                    | `F_clean_500k`   | < 5s                                                |
| PERF-03 | Review pane first paint, 500k rows                                           | `F_clean_500k`   | < 500ms                                             |
| PERF-04 | 50k visible detections — scroll                                              | `F_neg_many` ×50 | Smooth scroll, no jank                              |
| PERF-05 | 5M-row file upload (stretch)                                                 | Generated 5M cube| Backend memory stays under 4GB                      |
| PERF-06 | Apply on 500k rows with 1000 staged                                          | `F_clean_500k`   | < 10s                                               |

## D.3 Error handling

| ID     | Description                                                                  | Setup                                        | Expected                                                              |
| ------ | ---------------------------------------------------------------------------- | -------------------------------------------- | --------------------------------------------------------------------- |
| ERR-01 | Backend offline during upload                                                | Stop backend mid-upload                      | Retry banner with offline indicator                                   |
| ERR-02 | S3 credentials missing                                                       | Unset env vars on backend                    | 500 + generic user-facing error; full details in logs                 |
| ERR-03 | Network drop mid-apply                                                       | Kill network on client during apply          | Client retries; one audit log committed                               |
| ERR-04 | Bucket policy denies write                                                   | Bucket without write permission              | User-facing: "Unable to save — contact support"; logged               |
| ERR-05 | Manifest write race                                                          | Two near-simultaneous uploads                | One succeeds; other receives clear conflict response                  |

## D.4 API contract tests

For each endpoint in [`DATA_MODEL.md`](./DATA_MODEL.md), assert that:

| ID      | Endpoint                                                | Check                                                                            |
| ------- | ------------------------------------------------------- | -------------------------------------------------------------------------------- |
| API-01  | `GET /projects/{slug}`                                  | Returns manifest matching the schema in `DATA_MODEL.md` §Manifest                |
| API-02  | `POST /projects/{slug}/files`                           | Response includes `file_id` and presigned upload URL                             |
| API-03  | `POST /projects/{slug}/files/{file_id}/parse`           | Triggers parse; manifest updates                                                 |
| API-04  | `GET /projects/{slug}/files/{file_id}/schema`           | Returns role assignments and warnings                                            |
| API-05  | `PATCH /projects/{slug}/files/{file_id}/schema`         | Override accepted; re-validates                                                  |
| API-06  | `POST /projects/{slug}/files/{file_id}/detect`          | Runs detection; manifest status updates                                          |
| API-07  | `GET /projects/{slug}/files/{file_id}/detections`       | Returns paginated detections with cursor and total                               |
| API-08  | `POST /projects/{slug}/files/{file_id}/apply`           | Returns audit-log summary on success                                             |
| API-09  | `GET /projects/{slug}/files/{file_id}/cleaned-url`      | Returns presigned URL with short TTL                                             |
| API-10  | `GET /projects/{slug}/files/{file_id}/audit-url`        | Same                                                                             |
| API-11  | `GET /projects/{slug}/files/{file_id}/preview`          | Returns paginated cube cells matching the spreadsheet view spec                  |

---

# Part E — Deferred to dev time

Real but lower-priority cases that we'll address as they come up during build:

- Concurrent uploads to the same project (manifest race conditions beyond ERR-05)
- Re-running detection on a file that already has applied changes
- Unicode edge cases in customer / product names (RTL scripts, emoji, very long strings)
- Very wide cubes (1,000+ time columns)
- Cubes that are 95%+ zero (mostly empty)
- Single-time-column degenerate cube
- Float precision edge cases (`-0.0000001` — refund or noise?)
- Lineage tracking when a corrected version of a file is uploaded
- Multi-tenant separation (out of scope for the demo; relevant for production)
- Accessibility tests (keyboard navigation, screen readers, color-blind detector chips)
