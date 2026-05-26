"""End-to-end performance benchmark across canonical data sizes.

Run from the repo root:
    cd backend && uv run python scripts/bench.py

Measures schema inference, detection, and apply for each of:
- happy.parquet (100 rows, demo size)
- dirty.parquet (5k rows, mid canonical)
- mid-50k       (50k rows, synthetic in-between)
- stress.parquet (500k rows, upper bound)

Apply is run twice per fixture where feasible: with a realistic ~5% staged
sample (top by magnitude) and with every detection staged. The 5% case is
what a reviewer would typically commit. Stage-all on stress is skipped
because it peaks around 3.8 GB of memory locally — that workload runs on
Fly's 8 GB ``performance-2x`` VM, not on a dev laptop without an OOM risk.
"""

from __future__ import annotations

import gc
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.apply import Selection, apply_selections  # noqa: E402
from app.detect import detect_all  # noqa: E402
from app.schema import infer_schema  # noqa: E402
from data.generator import make_cube  # noqa: E402


def bench_one(name: str, df: pd.DataFrame) -> dict:
    print(f"\n=== {name}: {df.shape[0]:,} rows × {df.shape[1]} cols ===")
    out: dict = {"name": name, "rows": df.shape[0], "cols": df.shape[1]}

    t = time.perf_counter()
    sch = infer_schema(df)
    out["schema_s"] = time.perf_counter() - t
    print(f"  schema: {out['schema_s']:.3f}s")

    t = time.perf_counter()
    dets = detect_all(df, sch.roles.id_columns, sch.roles.measure_columns)
    out["detect_s"] = time.perf_counter() - t
    out["detections"] = len(dets)
    by_type = {"negative": 0, "refund": 0, "double_booking": 0, "outlier": 0}
    for d in dets:
        for t_ in d.flagged_by:
            by_type[t_] += 1
    print(f"  detect: {out['detect_s']:.2f}s  ({len(dets):,} detections)")
    print(
        f"    by detector: neg={by_type['negative']:,} ref={by_type['refund']:,} "
        f"dbl={by_type['double_booking']:,} out={by_type['outlier']:,}"
    )

    # Realistic apply: top ~5% by magnitude.
    sample_size = max(1, len(dets) // 20)
    sample = sorted(dets, key=lambda d: -abs(d.value))[:sample_size]
    selections = [Selection(d.detection_id, d.suggested_fix) for d in sample]
    t = time.perf_counter()
    result = apply_selections(
        df, dets, selections, file_id="bench", measure_columns=sch.roles.measure_columns,
    )
    out["apply_5pct_s"] = time.perf_counter() - t
    out["apply_5pct_changes"] = len(result.audit["changes"])
    print(
        f"  apply 5% ({sample_size:,} staged): {out['apply_5pct_s']:.2f}s  "
        f"({len(result.audit['changes']):,} audit entries)"
    )
    del result
    gc.collect()

    # Stage-all only for smaller sizes — skip stress to avoid local OOM risk.
    if len(dets) < 100_000:
        selections_all = [Selection(d.detection_id, d.suggested_fix) for d in dets]
        t = time.perf_counter()
        result = apply_selections(
            df, dets, selections_all, file_id="bench",
            measure_columns=sch.roles.measure_columns,
        )
        out["apply_all_s"] = time.perf_counter() - t
        out["apply_all_changes"] = len(result.audit["changes"])
        print(
            f"  apply all ({len(dets):,} staged): {out['apply_all_s']:.2f}s  "
            f"({len(result.audit['changes']):,} audit entries)"
        )
        del result
        gc.collect()
    else:
        print("  apply all: skipped — would peak ~3.8 GB on this size")

    return out


def main() -> None:
    df_happy = pd.read_parquet(REPO_ROOT / "data" / "scenarios" / "happy.parquet")
    df_dirty = pd.read_parquet(REPO_ROOT / "data" / "scenarios" / "dirty.parquet")
    print("Generating mid-50k cube…")
    df_mid = make_cube(
        rows=50_000, time_periods=24, seed=42, sparsity=0.4,
        negatives={"count": 200, "magnitude": "medium"},
        refunds={"count": 80, "style": "mixed"},
        double_bookings={"count": 30},
        outliers={"count": 80},
    )
    df_stress = pd.read_parquet(REPO_ROOT / "data" / "scenarios" / "stress.parquet")

    fixtures = [
        ("happy (100 rows)", df_happy),
        ("dirty (5k rows)", df_dirty),
        ("mid (50k rows)", df_mid),
        ("stress (500k rows)", df_stress),
    ]
    results = [bench_one(name, df) for name, df in fixtures]
    print()
    print("=" * 90)
    header = (
        f"{'Fixture':<22} {'rows':>8} {'detections':>12} "
        f"{'schema':>9} {'detect':>9} {'apply5%':>10} {'applyall':>10}"
    )
    print(header)
    print("-" * 90)
    for r in results:
        aa = f"{r['apply_all_s']:.2f}s" if "apply_all_s" in r else "skipped"
        print(
            f"{r['name']:<22} {r['rows']:>8,} {r['detections']:>12,} "
            f"{r['schema_s']:>8.2f}s {r['detect_s']:>8.2f}s "
            f"{r['apply_5pct_s']:>9.2f}s {aa:>10}"
        )


if __name__ == "__main__":
    main()
