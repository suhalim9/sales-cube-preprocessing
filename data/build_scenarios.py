"""Build the canonical scenario parquets used for stage demos.

Run from repo root: ``uv run --project backend python data/build_scenarios.py``.
Writes happy / dirty / stress files to ``data/scenarios/``. Deterministic
(seed 42) — re-running overwrites with identical content.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a script from anywhere: add repo root so ``data.generator``
# imports work.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from data.generator import make_cube  # noqa: E402

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"


def main() -> None:
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)

    scenarios = [
        (
            "happy.parquet",
            dict(
                rows=100,
                time_periods=32,
                seed=42,
                sparsity=0.2,
                negatives={"count": 20, "magnitude": "medium"},
                refunds={"count": 8, "style": "mixed"},
                double_bookings={"count": 4},
                outliers={"count": 5},
            ),
        ),
        (
            "dirty.parquet",
            dict(
                rows=5_000,
                time_periods=32,
                seed=42,
                sparsity=0.4,
                noise=0.05,
                negatives={"count": 250, "magnitude": "medium"},
                refunds={"count": 80, "style": "mixed"},
                double_bookings={"count": 40},
                outliers={"count": 120},
            ),
        ),
        (
            "stress.parquet",
            dict(
                rows=500_000,
                time_periods=32,
                seed=42,
                sparsity=0.5,
                negatives={"count": 2_000, "magnitude": "medium"},
                refunds={"count": 500, "style": "mixed"},
                double_bookings={"count": 200},
                outliers={"count": 800},
            ),
        ),
    ]

    for name, params in scenarios:
        path = SCENARIOS_DIR / name
        print(f"Generating {name} ({params['rows']:,} rows × {params['time_periods']} cols)…")
        df = make_cube(**params)
        df.to_parquet(path, index=False, compression="snappy")
        size_mb = path.stat().st_size / (1024 * 1024)
        print(f"  wrote {path.relative_to(SCENARIOS_DIR.parent.parent)}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
