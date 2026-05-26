# Demo Assumptions — Running List

A living document. Update as decisions are made. Each assumption notes **what** and **why** so we can revisit if reality differs.

---

## Contents

- [Project framing](#project-framing)
- [Tech stack](#tech-stack)
- [Hosting](#hosting)
- [Data shape](#data-shape)
- [Scale targets](#scale-targets)
- [Anomaly types](#anomaly-types)
- [Review-and-apply pattern (core mental model)](#review-and-apply-pattern-core-mental-model)
- [Data scenarios to build](#data-scenarios-to-build)
- [File formats](#file-formats)
- [UX assumptions](#ux-assumptions)
- [UI structure — three pages](#ui-structure--three-pages)
- [Stage 3 — Unified review (cell-level)](#stage-3--unified-review-cell-level)
- [Schema validation](#schema-validation)
- [Project layout](#project-layout)
- [Audit log](#audit-log)
- [Open questions](#open-questions-still-to-decide)

---

## Project framing

- **Primary audience: interview at Keye.** Secondary audiences (PE customers, internal stakeholders) should also find the demo plausible — meaning the workflow should *feel* real, not just look like a coding exercise.
- **Timeline: no fixed deadline, but ship soon.** Optimize for "good enough to present this week," not "production hardened."
- **Goal of the demo:** show a credible end-to-end cleaning workflow across all four anomaly types — negative values, refunds, double-bookings, and (added beyond the brief) statistical outliers.

## Tech stack

- **Frontend:** React + TypeScript + Vite + Tailwind + shadcn + TanStack Table + React Query.
- **Backend:** FastAPI + Pandas + PyArrow. Schema validation is hand-rolled in `app/schema.py` (Pandera was considered but the role-inference step on top of validation made a hand-rolled approach simpler).
- **Storage:** AWS S3 (uses existing AWS account). Bucket per environment.
- **Apply jobs:** Synchronous (no Celery/Redis for the demo — fine at 500k row scale; called out as production-time swap).
- **No Postgres / no DB for the demo.** Selections live in process memory; audit log written to S3 alongside the cleaned file.
- **Why slim:** the demo is about the workflow + detection logic, not infrastructure plumbing. Easy to point at the slim version and say "swap sync for Celery, add Postgres for cross-session state."

## Hosting

- **Frontend: Vercel Hobby** (free tier). Static React build deployed from the monorepo's `frontend/` dir.
- **Backend: Fly.io.** Deployed as `performance-2x` with 8 GB RAM. Apply on `stress.parquet` (~923k anomalies staged) peaks ~3.8 GB during audit-log JSON serialization, so 4 GB was on the edge; 8 GB gives safe headroom. Streaming the audit log would let this drop back to ~1.5 GB — deferred. Dockerized FastAPI app.
- **Storage: AWS S3** (existing account). Backend signs uploads / reads via IAM credentials stored as Fly secrets.
- **CI/CD:** GitHub Actions → Vercel auto-deploys on push; Fly deploys via `flyctl deploy` (manual or workflow).

## Data shape

- **Existing sample file is a PVM cube:** `customer × product_line × month`, 90 rows × 34 cols, months `2021_1` … `2023_8`, all values non-negative floats.
- **The sample file is clean** — zero negatives, no obvious double-bookings, no refund markers. So the demo cannot run against it as-is.
- **We will generate synthetic demo data** covering happy / dirty / stress cases. The original cube is the visual reference (preserve its shape).

## Scale targets

- **Build target: ~500k rows.** The demo should comfortably handle a 500k-row × ~32-col parquet (~16M cells) — load, detect, review, apply, download — without performance gymnastics.
- **Design target: 5M rows.** The case flags 5M as the upper bound the tool may need to handle. We build for 500k but **design the interfaces so 5M is a swap, not a rewrite**:
  - **Storage**: S3 from day one (not local FS). Streaming uploads via multipart, never buffer full file in FastAPI memory.
  - **Detection API**: results are **paginated server-side** (`GET /detections/{type}?cursor=...&limit=50`). Frontend never receives all detections at once.
  - **Review table**: **virtualized** (TanStack Table) — render only visible rows. Works the same for 500 detections or 500k.
  - **Detection logic**: **vectorized Pandas** only. No row-by-row Python loops. At 5M scale we'd swap `pd.read_parquet(...)` for chunked PyArrow row-group reads — but the detection functions themselves stay the same.
  - **Apply path**: writes the cleaned parquet via PyArrow. At 500k we can rewrite in memory; at 5M we'd stream row-groups. Same function signature.
- **What we explicitly don't do for the demo:** actually optimize the 5M hot path. We make sure nothing in the architecture would block it, but we don't prove it works on a 5M file unless time permits.
- **Reality check on data size:** Most uploaded cubes in real PE diligence are 10k–500k rows. The 5M case is the upper bound, not the median. Building for 500k covers ~80% of real files; the 5M-ready architecture covers the rest.

## Anomaly types

### Negative values
- Runs on the cube.
- Detection: any cell `< 0` in measure (monthly) columns.
- Default suggested fix: set the cell to `0`.

### Refunds
- A refund is a negative cell whose row has enough positive activity in earlier periods to absorb the reversal. The sale posts somewhere in the row's history; the refund reverses it.
- Detection: flag a negative when the cumulative positives in earlier columns of the same row ≥ `|negative|`. The cumulative-balance check is the guardrail: we only surface refunds the fix can actually unwind.
- Default fix: zero the refund cell, then walk backward through prior periods absorbing from positive cells until the magnitude is exhausted (most-recent-first). Each absorbed cell gets its own audit-log entry. Examples: `(200, -200) → (0, 0)`; `(10,000, -8,900) → (1,100, 0)`; `(600, 700, -1,200) → (100, 0, 0)`.
- **Negatives vs refunds:** both detectors can flag the same cell with the same fix. Negatives is the broad "any value `< 0`" net; refunds is the narrower "row has enough prior balance to be a real reversal" subset. Refund wins priority for `set_to_zero`. Different framing in the audit log — refund = "we removed a returned sale," negative = "we zeroed a likely data error."

### Double bookings
- Runs on the cube.
- Detection: a value `X` in month N adjacent to a `0` in **either** neighboring month (N-1 or N+1), where row-average activity suggests both months should have ~`X/2`. Pattern is "spike + empty neighbor on either side."
- Why either side: the duplicate could have landed in the period before or after the real entry. Restricting to "next column only" would silently miss last-period spikes and `(0, X)` shapes entirely.
- Suggested fixes: **split evenly** (default — split toward whichever neighbor is the zero) or **remove duplicate** (zero the spike, leave the neighbor alone).
- Edge: odd amounts (e.g., 101) → favor the spike side (`{101, 0}` → `{51, 50}`).

## Review-and-apply pattern (core mental model)

All three anomaly types share the same workflow:
1. **Detect** anomalies of the type.
2. Surface each with a **suggested fix**.
3. User **reviews** the list and **selects** which to apply (Accept = apply, Reject / leave unchecked = skip).
4. **Apply** writes the cleaned cube + appends to audit log.

The review-and-apply mechanic is identical across anomaly types. What differs is detection logic and default suggested fix.

## Data scenarios to build

Build three. Hand-craft synthetic cubes (deterministic seed):
- **Happy path** — small cube (~50–100 rows) with obvious, easy-to-explain anomalies for stage walkthrough.
- **Dirty / realistic** — larger cube (~1k–5k rows) with mixed anomalies that feels like real diligence data.
- **Stress test** — large cube (500k rows) to demonstrate streaming/perf.

Each scenario shipped as its own `.parquet` in `data/scenarios/`. Edge-case patterns (refund + negative on the same cell, ambiguous double-bookings, all-negative rows) are covered by the `F_*` fixtures in `data/test_fixtures/` rather than a separate canonical scenario.

## File formats

- **Parquet only for the demo.** CSV/Excel deferred — wire them if time allows.

## UX assumptions

- **UI polish: clean shadcn + Tailwind defaults.** Presentable, not distinctive.
- **No auth.** Project name acts as a lightweight identity / namespace proxy.
- **State persistence:** files, selections, audit logs persisted to S3 keyed by project name. Recent-projects list lives in browser localStorage. Refresh-safe within a project.
- **No DB for the demo.** Per-project `manifest.json` in S3 is the source of truth.
- **Single-user per file.** No locking, no multi-user collaboration.

## UI structure — three pages

### Page 1 — Gate
- Centered "What project are you working on?" input.
- Recent projects from localStorage (per-browser, no cross-user leak).
- Continue → Page 2.

### Page 2 — Project (file explorer)
- Lists files in this project with status (`⊘ upload pending` · `◇ schema confirm` · `⊙ detected` · `✓ cleaned`).
- Each entry shows row count, anomaly count, last-updated timestamp.
- `[+ Upload new file]` button.
- Click a file → Page 3.

### Page 3 — File workspace
Persistent **top stepper** with 4 stages. Click any completed stage to revisit.

1. **Schema** — auto-detected role assignments + soft warnings. `[Adjust column roles]` modal for overrides. `[Run detection]` advances.
2. **Detect** — brief progress, then a triage summary card (counts per anomaly type with $ magnitude and concentration hints). `[Start reviewing]` advances.
3. **Review** — **unified cell-level review** (see below). The main work happens here.
4. **Apply** — final summary across all anomaly types, confirmation, then commit. Writes cleaned parquet + audit JSON to S3.

A **"View data" drawer** is accessible from any stage — a right-side slide-over with the full cube as a virtualized spreadsheet, anomaly cells color-coded. The Excel-native verify view.

## Stage 3 — Unified review (cell-level)

The review screen is a single virtualized table where each row = one detected anomaly cell.

- **Color flag column** shows which detector(s) hit the cell: 🟥 negative, 🟧 refund, 🟦 double-booking, 🟪 outlier. Multiple dots per row when overlap.
- **Filter chips** at top toggle anomaly types (single-select today; multi-select listed as future work in ROADMAP §10).
- **No sort controls** in the built UI; row order follows detection-index. Sort by magnitude / customer / period / confidence is future work.
- **Action column adapts per row:** clicking a cell stages it with its primary detector's default action; the **Staged changes** bar at the bottom shows one pill per detector that flagged the cell (with `Keep as is` always rendered last as the conservative bail-out).
  - Negative-only → `Set to 0` · `Keep as is`.
  - Refund (with or without negative) → `Set to 0` · `Apply refund` · `Keep as is`. `Apply refund` triggers FIFO walk-back against prior positive periods.
  - Double-booking → `Split evenly` · `Keep as is`.
  - Outlier-only → `Keep as is` · `Set to 0`. Default is `Keep as is` — outliers may be legitimate spikes.
- **Bulk actions:** `Stage all visible` · `Unstage all visible` · `Clear staged`. The "all visible" labels respect the active filter chip.

Finer details (exact wording of conflict labels, modal flows, edge-case action UIs, etc.) deferred to development — to be worked out as we build.

## Schema validation

- **Validation runs between parse and detection.** A dedicated step that gates the rest of the workflow.
- **Strictness: flexible auto-detect.** Tool inspects the uploaded file and infers column roles:
  - **Identifier columns** — string/object dtype, used as row keys (e.g., `customer`, `product_line`).
  - **Time columns** — column-name regex match against common patterns (`YYYY_M`, `YYYY-MM`, `Jan-21`, `2021Q1`, etc.). Order inferred.
  - **Measure columns** — numeric, non-time. The cube's actual data.
  - User can manually re-assign roles if auto-detect is wrong.
- **Schema confirmation: visible, required.** After upload, show a card with:
  - Detected ID columns, time columns (range + cadence), measure count, row count.
  - Soft warnings (empty rows, mostly-null columns, time gaps, mixed dtypes).
  - "Looks right — start detection" / "Adjust column roles" actions.
  - User must confirm before detection runs.
- **Hard checks (block):** parseable file; ≥1 ID col; ≥1 measure col; ≥1 time col; no duplicate ID-tuple rows; not empty; measure columns coerce to numeric.
- **Soft checks (warn):** all-zero rows, >90% null columns, non-contiguous time sequence, mixed dtypes in a column.
- **Implementation: hand-rolled in `app/schema.py`.** Role inference and hard/soft checks are written as direct Pandas operations. Pandera was considered but the role-inference step sits on top of validation, which made a unified hand-rolled module simpler to maintain.

## Project layout

See the current layout in `README.md` — it's the canonical source.

## Audit log

- **Format: JSON sidecar in S3.** Written alongside the cleaned parquet on apply.
- **One audit entry per applied change.** Fields:
  `change_id, anomaly_type, row_key (customer+product), column (time), value_before, value_after, suggested_fix, applied_at, user_id (placeholder)`.
- Audit log is downloadable with the cleaned file.

## Open questions (still to decide)

*None — ready to write the roadmap.*
