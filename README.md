# Sales Cube Cleaning Tool

A small web demo for cleaning PE-diligence sales cubes (`customer × product × period`) before they feed downstream analyses like PVM, revenue bridges, and customer concentration.

You upload a Parquet, run four detectors against it, review every flagged cell, apply the fixes you want, and download a cleaned cube alongside a per-change audit log.

Built as a take-home for the Keye technical interview.

**Live demo:** [https://sales-cube-preprocessing.vercel.app](https://sales-cube-preprocessing.vercel.app/)

---

## Contents

- [A 30-second tour](#a-30-second-tour)
- [Top considerations](#top-considerations)
- [Companion docs](#companion-docs)
- [System at a glance](#system-at-a-glance)
- [The four detectors](#the-four-detectors)
- [Decisions worth calling out](#decisions-worth-calling-out)
- [Performance](#performance)
- [Scope and future work](#scope-and-future-work)
- [Running it locally](#running-it-locally)
- [Repo layout](#repo-layout)
- [Tests and test data generation](#tests-and-test-data-generation)

---

## A 30-second tour

The app opens on a **gate** — a project-name input. With no auth in the demo, the project name acts as a lightweight namespace so state (files, manifests, audit logs) can be scoped without a user model. Recent projects are cached in localStorage so you don't have to retype. **Enter any project name to get started.**

Inside the workspace, the workflow is a four-stage stepper:

- **Schema** — the app infers column roles, which you confirm or override.
- **Detect** — all four detectors run and surface counts and magnitudes per type.
- **Review** — each flagged cell appears as one virtualized row, color-coded by detector.
- **Apply** — your selections write atomically as `cleaned.parquet` and `audit.json` to S3.

---

## Top considerations

Beyond the detector logic itself, here's what I deliberately invested time in:

1. **Planning and design before coding.** Significant time went into the workflow spec, API contract, and test matrix before any implementation — all captured in the [companion docs](#companion-docs) below.

2. **Schema validation for input files.** Real diligence cubes arrive with inconsistent column names, mixed dtypes, and missing time periods. A dedicated parse-and-confirm step infers column roles, surfaces hard failures, and lets the user override before detection runs — so the rest of the pipeline can trust its inputs. (More in [`ASSUMPTIONS.md`](./ASSUMPTIONS.md) and [`TEST_SCENARIOS.md`](./TEST_SCENARIOS.md).)

3. **Performance at scale.** I tested the end-to-end pipeline on a 500k-row cube containing **923k anomalies** — detection, paginated review, apply, and audit-log generation all stay responsive.

4. **Thorough testing.** Detection is exercised against hand-crafted edge cases (refund + negative on the same cell, ambiguous double-bookings, all-negative rows, stress data) plus the three canonical demo scenarios. Unit tests cover detector math, schema validation (hard + soft checks), the apply pipeline, audit-log construction, manifest persistence, and the cache-healing paths after restart. Full matrix in [`TEST_SCENARIOS.md`](./TEST_SCENARIOS.md).

---

## Companion docs

Four longer-form docs back up this README. If you only open one, **`ROADMAP.md`** is the place to start.

| Doc                                        | What's in it                                                                    |
| ------------------------------------------ | ------------------------------------------------------------------------------- |
| [`ROADMAP.md`](./ROADMAP.md)               | Full design doc — problem framing, decisions, user stories, detector algorithms |
| [`DATA_MODEL.md`](./DATA_MODEL.md)         | S3 layout, manifest schema, audit schema, REST API contract                     |
| [`ASSUMPTIONS.md`](./ASSUMPTIONS.md)       | Running list of working assumptions and the reasoning behind each               |
| [`TEST_SCENARIOS.md`](./TEST_SCENARIOS.md) | Parameterized data generator and the full test matrix                           |

---

## System at a glance

```
 Browser ──► Vercel (Vite SPA)
              │
              ├──► presigned PUT ────► S3
              │
              └──► REST ──────────► Fly.io (FastAPI) ──► S3 (read/write/manifest)
```

**Stack:** React · TypeScript · Tailwind · shadcn · TanStack Table · React Query · FastAPI · Pandas · PyArrow · S3.

---

## The four detectors

| Detector                           | Logic                                                                                        | Default fix                                        |
| ---------------------------------- | -------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| Negative                           | Cell `< 0`                                                                                   | Set to 0                                           |
| Refund                             | Negative whose row has enough prior positive activity to absorb it                           | Walk back through prior periods, most-recent first |
| Double-booking                     | Spike adjacent to a zero (either side); row average suggests both periods should be ~spike/2 | Split evenly                                       |
| Outlier _(added beyond the brief)_ | Per-row statistical check (IQR)                                                              | Keep as is OR Set to 0 available                   |

A single cell can be flagged by more than one detector. The review table shows every hit as a chip, and any conflicts at apply time are resolved server-side (see below).

---

## Decisions worth calling out

### 1. Built for 500k rows

Stress-tested at 500k rows with 923k anomalies.

- The browser uploads straight to S3 via a signed URL — bytes never pass through the server.
- Long lists page by cursor (a bookmark), not by skipping ahead, so page 100 is as fast as page 1 — i.e. cursor pagination instead of offset/limit.
- The review table only renders rows on screen (virtualized), so it stays smooth at any size.
- Detection runs as bulk column operations (vectorized Pandas), not row-by-row — roughly 100× faster.
- Files are read and written in chunks (Parquet row-groups), so the whole file never has to sit in memory.

### 2. Different detectors can flag the same cell

More than one detector can flag a cell simultaneously. Rather than picking one detector to "win" silently, every flag shows up as a chip and the user decides which fix to apply. Users may want to go back and forth or change their minds, thus we allow them to switch anomaly modes. If the user picks conflicting fixes for the same cell, the system flags it rather than silently resolving one. They can also change the fix at the bottom panel.

### 3. Audit log as a first-class artifact

`audit.json` ships beside `cleaned.parquet`, with one entry per applied change — including `keep_as_is` no-ops, so the log captures what was _reviewed_, not just what changed.

---

## Performance

Key backend levers: detectors run concurrently via `ThreadPoolExecutor`, `set_to_zero` applies as bulk pandas column writes (not per-cell), and the audit log is serialized with `orjson` then gzipped (~3× faster serialize, ~10× smaller on the wire). DataFrames and detections are cached in-process and persisted to S3 as a versioned sidecar so revisits skip detection entirely.

Worst-case timings, M-class CPU, every detection staged.

| Fixture          |    Rows | Detections | Schema | Detect | Apply | Peak RSS |
| ---------------- | ------: | ---------: | -----: | -----: | ----: | -------: |
| `happy.parquet`  |     100 |        210 |  0.01s |  0.00s | 0.01s |        — |
| `dirty.parquet`  |   5,000 |      8,708 |  0.01s |  0.03s | 0.07s |   200 MB |
| mid-50k          |  50,000 |     62,933 |  0.03s |  0.26s | 0.49s |   600 MB |
| `stress.parquet` | 500,000 |    923,219 |  0.35s |  3.64s | 5.66s |   2.3 GB |

- **Detect** scales sub-linearly — detectors run in parallel and the slowest (outliers, IQR per row) dominates.
- **Apply** scales with staged count, not row count. Bulk pandas column writes for `set_to_zero`; per-cell only for refund cascades and splits.
- **Stress** fits in the 8 GB `performance-2x` Fly VM with headroom. Streaming the audit log would drop the peak to ~1.5 GB.

Reproduce: `cd backend && uv run python scripts/bench.py`.

**Fixtures.** `happy.parquet` and `dirty.parquet` are committed. `mid-50k` is synthesized inline by the bench script (`make_cube(rows=50_000, seed=42, …)`). `stress.parquet` (~80 MB) is gitignored — regenerate after cloning with `uv run --project backend python data/build_scenarios.py`. All generation is deterministic (seed 42), so the numbers above are reproducible.

---

## Scope and future work

This build prioritized end-to-end workflow depth over feature breadth, tested at 500k rows with 923k anomalies. What's _not_ in this version splits into three categories:

| Category                                                                  | Examples                                                                                                           |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| **Out of product scope** — doesn't fit a single-analyst cleaning workflow | Multi-user collaboration, public API, bulk file processing                                                         |
| **Production hardening** — would change for a real deployment             | Postgres for state, Celery + Redis for apply, real auth, S3 Object Lock, `Decimal` money math, observability stack |
| **Future work** — next features if continuing                             | Real auth, CSV/Excel ingestion, saved presets, async apply, outlier tuning (top 5 below)                           |

**Top 5 next steps** (in priority order):

1. Real auth with `user_id` populated in the audit log (field already exists, stubbed).
2. CSV and Excel ingestion behind the same upload contract.
3. Saved review presets ("accept all negatives under $X") reusable across files.
4. Apply on a background job queue (Celery + Redis), so large files don't block the request thread.
5. Outlier detector tuning — learned per-column thresholds instead of per-row IQR.

Full breakdown — production-hardening deltas, every future-work direction, and accepted environmental assumptions — lives in [ROADMAP §10 — What's not in the demo](./ROADMAP.md#10-whats-not-in-the-demo).

---

## Running it locally

**Prereqs:** Python 3.12+, Node 20+, [`uv`](https://docs.astral.sh/uv/), [`pnpm`](https://pnpm.io/), and AWS credentials with S3 access.

```bash
# Backend
cd backend && uv sync
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... S3_BUCKET=...
uv run uvicorn app.main:app --reload --port 8000

# Frontend (new shell)
cd frontend && pnpm install && pnpm dev   # http://localhost:5173

# Generate demo data (deterministic seeds, three scenarios: happy / dirty / stress)
# Note: stress.parquet (~80 MB) is gitignored — this command regenerates it locally.
cd data && python build_scenarios.py
```

---

## Repo layout

```
backend/         FastAPI + detectors + apply pipeline (uv, Docker, fly.toml)
frontend/        React + Vite (pnpm → Vercel)
data/
  ├── source/    Original PVM cube
  ├── scenarios/ Generated demo files (happy / dirty / stress)
  └── test_fixtures/

ASSUMPTIONS.md     Running list of working assumptions and why
DATA_MODEL.md      S3 layout, manifest schema, audit schema, REST contract
ROADMAP.md         Full design doc — problem framing, decisions, user stories, detectors
TEST_SCENARIOS.md  Parameterized generator + full test matrix
```

If you want the long version of any of the decisions above, **`ROADMAP.md`** is the place to look.

---

## Tests and test data generation

```bash
cd backend && uv run pytest
```

Test fixtures and demo scenarios share one generator (`data/build_test_fixtures.py` and `data/build_scenarios.py`), so test data and demo data come from a single source of truth.
