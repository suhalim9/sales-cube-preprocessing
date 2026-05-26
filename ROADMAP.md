# Sales Cube Cleaning Tool — Demo Roadmap

Roadmap for the interview demo. The tool cleans aggregated `customer × product × period` sales cubes used in PE revenue diligence. Cleaned output feeds downstream analyses: PVM (paired with units data), revenue bridges, customer concentration, cohort and mix-shift. Reference docs (data model, API contract) live alongside this file.

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Problem & motivation](#2-problem--motivation)
3. [Design decisions & thinking process](#3-design-decisions--thinking-process)
4. [User stories & acceptance criteria](#4-user-stories--acceptance-criteria)
5. [UX flows](#5-ux-flows)
6. [Detection algorithms](#6-detection-algorithms)
7. [Tech stack & architecture](#7-tech-stack--architecture)
8. [Test scenarios](#8-test-scenarios)
9. [Risks & mitigations](#9-risks--mitigations)
10. [What's not in the demo](#10-whats-not-in-the-demo)
11. [Open questions](#11-open-questions)
12. [Glossary](#12-glossary)

Related docs:

- [`DATA_MODEL.md`](./DATA_MODEL.md) — S3 layout, manifest schema, audit log schema, REST API contract
- [`TEST_SCENARIOS.md`](./TEST_SCENARIOS.md) — parameterized data generator, canonical scenarios, full test matrix
- [`ASSUMPTIONS.md`](./ASSUMPTIONS.md) — running list of working assumptions

---

## 1. Executive summary

A web tool that lets a PE associate upload a sales cube (Parquet), detect data-quality anomalies, review and selectively apply suggested fixes, and export a cleaned cube with an audit log.

Four detectors. **Three come from the case description** — negative values, refunds (pattern-detected, no labels assumed), and double-bookings. **The fourth is our addition** — statistical outliers (IQR per row) — as a generic catch-all for anomalies the three explicit detectors miss. Review happens in a single unified table — one row per detected cell, color-coded by detector(s), with an adaptive per-row action column.

Built for 500k-row files; architecture designed for 5M. Three pages: gate (project entry) → project (file list) → file workspace (4-stage stepper: Schema → Detect → Review → Apply).

---

## 2. Problem & motivation

PE associates working on **revenue diligence** receive aggregated sales cubes (`customer × product × period`) from target companies. These cubes feed downstream analyses: PVM (Price/Volume/Mix), revenue bridges, customer concentration, cohort and mix-shift. Raw data is often messy — data-entry errors (negatives), embedded refunds that distort top-line revenue, double bookings, and statistical anomalies that may be one-time items or genuine errors. Garbage in, garbage out.

The cube has to be cleaned before downstream analyses are trustworthy, and every change needs an audit trail because the work is reviewed.

### Assumed users

Inferred from context (Keye's PE focus, the `pvm_cube_input` filename), not validated through user research. Worth confirming with real users before betting design decisions on them.

**Primary — PE associate.** Junior investment professional, owns the cleaning workflow.

**Secondary — VP / principal / partner.** Reviews the associate's output and the audit log. Doesn't use the tool day-to-day. Needs clear before/after, per-change attribution to a detector, confidence in the audit trail.

### What this tool does

- Detects common anomalies (negatives, refunds, double-bookings, outliers).
- Surfaces suggested fixes for per-row review.
- Captures an audit log by construction — every applied change attributed to a specific detector.
- Outputs a cleaned cube ready for downstream analysis.

---

## 3. Design decisions & thinking process

The calls that shaped the build, with the trade-offs that informed each one.

### 3.1 Data shape: cube only

Alternatives considered: (A) cube + transactional file, (B) refunds as negatives inside the cube, (C) gross/refund/net column triplets.

**Chose cube-only.** The sample data is a cube and the tool is built for that shape. Transactional support could be added later. Trade-off: refund detection works from a balance-aware paired-reversal pattern on the cube itself (a negative cell whose cumulative prior positive activity in the same row covers it) rather than per-transaction reversal matching.

### 3.2 Schema validation

After upload but before detection, the tool inspects the file, infers each column's role, and shows the user what it detected for confirmation or override.

**Detected roles:**

- **Identifier columns** — string / object dtype with a bounded set of distinct values, used as row keys (e.g., `customer`, `product_line`).
- **Time columns** — column names matching common patterns (`YYYY_M`, `YYYY-MM`, `Jan-21`, `2021Q1`); sorted chronologically to surface gaps in the sequence.
- **Measure columns** — numeric (float or int), neither identifier nor time.

**Hard checks** (block detection until resolved):

- At least one column of each role is detected.
- No duplicate identifier-tuple rows (e.g., the same `(customer, product_line)` appearing twice).
- All measure columns successfully coerce to numeric.
- The file is not empty and has no duplicate column names.

**Soft warnings** (surfaced to the user but don't block):

- Missing periods in the time sequence (e.g., `2022_5` absent between `2022_4` and `2022_6`).
- Time columns mixing formats in the same file (`2022_1` alongside `Jan-22`).
- Columns that are mostly null (>90%).
- Rows that are entirely zero across all measures.
- A column with mixed dtypes (some strings, some numbers).

### 3.3 Pattern detection for the case-given anomalies

The case specifies three anomaly types — negatives, refunds, double-bookings — detected by structural patterns on the cube. Specs in §6.

### 3.4 Added a statistical outlier detector (IQR per row)

A fourth detector beyond the case's three — catch-all for what they miss. Suggested action is a flag, not a value change — outliers may be legitimate spikes.

IQR per row, picked over Z-score, MAD, Isolation Forest, LOF, DBSCAN, and time-series methods. Reasons: robust to skew (financial data isn't Gaussian, so Z-score is wrong), and interpretable as the box-plot rule every analyst already knows. Trade-off: single-variable, no cross-row patterns.

### 3.5 Review UI: side-by-side Before / After

Two tables side by side, both showing the cube in the same format. Left is the original cube; right is the cube with proposed fixes applied. Anomaly cells are colored by detector type. The user clicks anomaly cells to toggle which fixes to apply. Clicking also scrolls both sides to the same row, so neighboring rows are visible for context. Only the visible rows load from the backend.

### 3.6 Overlap handling: detect once, resolve inline

Cells flagged by multiple detectors appear as one row with multiple chips. Action column: single checkbox if detectors agree on the fix, radio picker if they conflict. Audit log attributes each applied change to a specific detector. Rejected alternative: re-detect after each apply — counts would shift mid-workflow with no undo.

### 3.7 Scale

The agreed upper bound from the case discussion is **5M rows**. For the demo we handle **500k rows** in memory with Pandas. Anything beyond 5M is production work, covered in §10.

Reference for the numbers involved:

| Scale                   | In memory  | On disk    |
| ----------------------- | ---------- | ---------- |
| 500k rows (demo target) | ~150–200MB | ~15–50MB   |
| 5M rows (upper bound)   | ~1.5–2GB   | ~150–500MB |

### 3.8 Identity: project name as namespace, no auth

Per the case discussion, no authentication is required. Project name acts as an S3 namespace and as a localStorage key on the client. Trade-off: anyone with the URL can use any project name. Real auth would replace this in production.

### 3.9 Tech stack

This is what the demo runs on. It's deliberately minimal — only what's needed to demonstrate the workflow — not a production architecture.

- Frontend: React, hosted on Vercel
- Backend: FastAPI, hosted on Fly.io
- Storage: AWS S3

Production additions are covered in §10.

### 3.10 Demo data: generator + canonical scenarios

Synthetic data generator (`data/generator.py`) parameterized by row count, anomaly density per type, seed. Commit 3 canonical scenarios to `data/scenarios/` for stage demos; generate anything else on demand.

---

## 4. User stories & acceptance criteria

### Story 1 — Start a new project

> _As a PE associate, I want to start a new diligence project so my files for one target are grouped together._

**Acceptance:**

- Gate page accepts a new project name and continues to the project page.
- Project name persists to localStorage as a recent project.
- Empty file list shown on first visit.

### Story 2 — Enter an existing project

> _As a PE associate, I want to enter existing workspace without losing my files or changes._

**Acceptance:**

- Recent projects from localStorage shown on the gate (name + file count + last-active).
- Clicking a recent project loads its manifest from S3 with file statuses preserved.

### Story 3 — Upload a cube

> _As a PE associate, I want to upload a Parquet cube so the tool can detect anomalies in it._

**Acceptance:**

- Drag-drop or file picker accepts `.parquet`.
- Reject `>2GB`, non-Parquet, or unparseable files with clear errors.
- File appears in the project list with status `⊘ upload pending` → `◇ schema confirm` after parse.

### Story 4 — Confirm the schema

> _As a PE associate, I want to see what the tool detected as my columns so I can correct it if it's wrong._

**Acceptance:**

- Stage 1 displays detected ID, time, and measure columns plus any soft warnings.
- "Adjust column roles" opens a modal for manual override.
- "Run detection" advances to Stage 2.

### Story 5 — Triage detected anomalies

> _As a PE associate, I want a fast overview of what was found so I know where to focus._

**Acceptance:**

- Stage 2 shows counts per detector with $ magnitude and a concentration hint ("70% in 2022", "8 of 12 in Customer_3").

### Story 6 — Review and select fixes

> _As a PE associate, I want to review all anomalies in one place and select which suggested fixes to apply._

**Acceptance:**

- One virtualized table; one row per detected cell.
- Color-coded flag chips per row showing the detector(s).
- Filter chips at top to toggle detector types (single-select today; multi-select is future work).
- Clicking a cell stages it with its primary detector's default; the Staged-changes bar at the bottom shows one pill per detector that flagged the cell so the analyst can switch the attribution (and the action) before applying. Available actions: `Set to 0`, `Apply refund`, `Split evenly`, `Keep as is`.
- "Select all visible" applies default action to filtered rows.

### Story 7 — Apply changes

> _As a PE associate, I want to commit my selected fixes and download the result._

**Acceptance:**

- Stage 4 shows a summary grouped by detector with overlap counts.
- Apply writes cleaned `.parquet` + audit `.json` to S3 atomically.
- On success, download links shown; file status updates to `✓ cleaned`.

### Story 8 — See an anomaly in context

> _Before applying a fix, I want to see the flagged cell's neighbors in the actual cube so I can judge whether the fix makes sense._

**Acceptance:**

- Clicking a detection in the review table opens a spreadsheet view of the cube, scrolled to that cell.
- The user can scroll around to inspect neighboring rows and months — Excel-like.
- Backend loads only the visible window of cells; works at 500k+ row scale.
- Anomaly cells in the spreadsheet are color-coded by detector.

### Story 9 — Download to share

> _As a PE associate, I want to download the cleaned cube and the audit log so I can share them with reviewers or pass them into downstream tools._

**Acceptance:**

- Cleaned cube downloads as standard Parquet (no proprietary format).
- Audit log downloads as JSON, with one entry per applied change and detector attribution.
- Both files are accessible from the cleaned file's project entry at any time.

---

## 5. UX flows

### Page 1 — Gate

```
┌─────────────────────────────────────────────────────────────┐
│                      Sales Cube Cleaner                      │
│                                                              │
│              What project are you working on?                │
│              ┌────────────────────────────┐                  │
│              │ Acme diligence Q1          │                  │
│              └────────────────────────────┘                  │
│                       [ Continue ]                           │
│                                                              │
│              Recent projects                                 │
│              · Acme diligence Q1   (2 files, 2d)             │
│              · Beta Holdings       (1 file, 5d)              │
└─────────────────────────────────────────────────────────────┘
```

### Page 2 — Project (file explorer)

```
┌─────────────────────────────────────────────────────────────┐
│  Acme Diligence Q1                       [Switch project]   │
├─────────────────────────────────────────────────────────────┤
│  Files (3)                            [+ Upload new file]   │
│                                                              │
│  ✓  sales.parquet           487k rows   cleaned · 2d ago     │
│  ⊙  marketing-spend.parquet 124k rows   82 anomalies         │
│  ⊘  gl-trial.parquet         18k rows   schema needs confirm │
└─────────────────────────────────────────────────────────────┘
```

### Page 3 — File workspace, Stage 3 (Review)

```
┌────────────────────────────────────────────────────────────────┐
│  Acme Q1 › sales.parquet                            [Help] [✕] │
├────────────────────────────────────────────────────────────────┤
│  ●─── ●─── ●─── ○                                              │
│  Schema  Detect  Review  Apply                                 │
├────────────────────────────────────────────────────────────────┤
│  Filter: 🟥 Neg(47)  🟧 Ref(12)  🟦 Dbl(8)  🟪 Out(15)         │
│  Staged: 65 of 82                                              │
├────────────────────────────┬───────────────────────────────────┤
│   Before (original cube)   │  After (with selected fixes)      │
├────────────────────────────┼───────────────────────────────────┤
│ Cust   Prod  2022_5  2022_6│ Cust   Prod  2022_5  2022_6       │
│ Cust_3 A   [-1,247]🟥 3,500│ Cust_3 A      [0]☑   3,500        │
│ Cust_3 B   [-10k]🟥🟧 1,500│ Cust_3 B      [0]☑   1,500        │
│ Cust_5 A    8,000   [0]🟦  │ Cust_5 A    [4,000]☑ [4,000]☑     │
│ Cust_12 C  [-50k]🟪  12,000│ Cust_12 C  [-50k]⚑  12,000        │
│ ...                        │ ...                               │
├────────────────────────────┴───────────────────────────────────┤
│ [Select all visible]    [Clear staged]    [Apply staged]       │
└────────────────────────────────────────────────────────────────┘
```

### How the side-by-side view works

The Before pane (left) shows the cube as uploaded. The After pane (right) shows the same cube with the user's currently-selected fixes already applied. Both panes scroll in lockstep, so the same rows and columns are visible on both sides at all times — the user sees neighboring rows by default, no extra clicks. Anomaly cells are color-coded by detector type in the Before pane; cells that have a selected fix show the result value with a checkmark in the After pane. Clicking an anomaly cell on either side toggles whether to apply that fix. Only the visible window of rows is loaded from the backend, so the design works at the 500k-row demo target.

**Performance budget:**

| Operation               | 500k rows            | 5M rows (design target) |
| ----------------------- | -------------------- | ----------------------- |
| Upload (parse → S3)     | <10s                 | <60s (streamed)         |
| Detection (all 4 types) | <5s                  | <30s                    |
| Render review table     | <500ms (virtualized) | <500ms                  |
| Apply                   | <10s                 | <60s                    |

---

## 6. Detection algorithms

### Negatives

```python
neg_mask = df[measure_cols] < 0
```

Fix: set to `0`. Universal catch-all; defers "data error vs refund" judgment to the review UI's color flags.

### Refunds

A negative cell whose row has enough positive activity in earlier periods to absorb the reversal — the sale posts in some prior period, the refund reverses it.

```python
neg = df[measure_cols] < 0
positives = df[measure_cols].clip(lower=0)
prior_balance = positives.cumsum(axis=1).shift(1, axis=1).fillna(0)
refunds = neg & (prior_balance >= df[measure_cols].abs())
```

The cumulative-balance check means we only surface refunds the cleaning fix can actually unwind. A `-8,900` against `550` of prior sales is *not* surfaced as a refund (the negatives detector still catches it as a data-error candidate).

**Fix:** zero the refund cell and walk backward through prior periods, absorbing from positive cells one at a time until the magnitude is exhausted. Most-recent-first matching mirrors how a refund typically reverses the closest prior sales activity. The detection guarantee means the loop always completes.

| Before                                      | After                                       |
| ------------------------------------------- | ------------------------------------------- |
| `…, 200, -200, …`                           | `…, 0, 0, …`                                |
| `…, 10,000, -8,900, …`                      | `…, 1,100, 0, …`                            |
| `…, 600, 700, -1,200, …`                    | `…, 100, 0, 0, …`                           |
| `…, 10,000, 0, 0, 550, -8,900, …`           | `…, 1,650, 0, 0, 0, 0, …`                   |

Each absorbed positive cell becomes its own audit-log entry so the analyst can see exactly which past sales the refund was matched against.

Audit attribution differs from negatives — "refund" = "we removed a returned sale"; "negative" = "we zeroed a likely data error." Same cell can be flagged by both; refund wins priority for `set_to_zero` since refund is the more specific signal.

### Double bookings

Pattern: a strictly positive value `X` adjacent to a `0` in **either** neighboring month, where `X` is anomalously large vs the row average. Both shapes `(X, 0)` and `(0, X)` count — the duplicate could land either side of the real entry, and the last-period spike is just as suspicious as the first-period one.

```python
row_mean_nonzero = row_vals[row_vals > 0].mean()
is_spike = (row_vals[i] > 0) & (row_vals[i] > 2 * row_mean_nonzero)
zero_neighbor = (i > 0 and row_vals[i-1] == 0) or (i < n-1 and row_vals[i+1] == 0)
double_booking = is_spike & zero_neighbor
```

Fixes: **split evenly** (default, `{X, 0}` → `{X/2, X/2}` — toward whichever neighbor is the zero) or **remove duplicate** (set spike to 0). Odd amounts split favoring the spike side (`{101, 0}` → `{51, 50}`).

### Outliers (IQR per row)

```python
Q1 = df[measure_cols].quantile(0.25, axis=1)
Q3 = df[measure_cols].quantile(0.75, axis=1)
IQR = Q3 - Q1
outliers = (df[measure_cols] < (Q1 - 1.5*IQR)) | (df[measure_cols] > (Q3 + 1.5*IQR))
```

Action: **flag for review** (no value change). Outliers can be legitimate spikes; auto-fixing them is dangerous. Audit log records the flag with `value_before == value_after`.

---

## 7. Tech stack & architecture

```
┌──────────────┐  HTTPS  ┌─────────────────┐   S3   ┌──────────┐
│   Vercel     ├────────►│     Fly.io      ├───────►│   AWS    │
│  (React app) │         │   (FastAPI)     │        │    S3    │
└──────────────┘         └─────────────────┘        └──────────┘
```

| Layer        | Choice                                                                             | Reason                                                            |
| ------------ | ---------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Frontend     | React + TypeScript + Vite + Tailwind + shadcn/ui + TanStack Table + TanStack Query | Standard stack, virtualized table is non-negotiable at 500k+ rows |
| Backend      | FastAPI + Pandas + PyArrow + boto3                                                 | Vectorized detection; schema validation hand-rolled in `app/schema.py` because role inference sits on top of declarative checks |
| Storage      | AWS S3                                                                             | Existing account; matches production target                       |
| Hosting (FE) | Vercel Hobby                                                                       | Free, zero config                                                 |
| Hosting (BE) | Fly.io                                                                             | ~$2/month, no cold starts, Docker deploy                          |
| Metadata     | `manifest.json` per project in S3                                                  | No DB needed for the demo                                         |
| Apply        | Synchronous in request thread                                                      | Fine at 500k row scale; Celery swap is in scope for production    |

**Scale strategy.** All "5M-ready" interfaces present from day one: S3 object store, paginated detection API (`GET /detections?cursor=...&limit=50`), virtualized review table, vectorized Pandas. We don't optimize hot paths for 5M; we make sure none of the architecture blocks it.

Full data model, S3 layout, manifest schema, audit log schema, and REST API contract live in [`DATA_MODEL.md`](./DATA_MODEL.md).

---

## 8. Test scenarios

Test scenarios — including the parameterized data generator, canonical demo files, and the full test matrix (upload, schema validation, per-detector detection cases, review UI, apply, error handling, audit log) — live in [`TEST_SCENARIOS.md`](./TEST_SCENARIOS.md).

---

## 9. Risks & mitigations

| Risk                                                          | Likelihood | Impact | Mitigation                                                                                                                                           |
| ------------------------------------------------------------- | ---------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| 5M-row file OOMs the Fly machine                              | Medium     | High   | 8 GB `performance-2x` machine, sized empirically (apply on 923k staged anomalies peaks ~3.8 GB during audit-log serialization). 5M not benchmarked; PyArrow row-group reads are wired in but not yet auto-triggered by row count; explicit memory guardrails are future work. |
| Detection false positives erode trust (over-flagging refunds) | Medium     | Medium | User reviews each detection before applying; outlier detector is flag-only (no auto-fix); refund detector restricted to cases where prior balance covers the reversal. |
| Detection misses real anomalies (under-flagging)              | Medium     | High   | Outlier detector serves as the generic catch-all for patterns the three explicit detectors miss; "View data" drawer lets the analyst eyeball.        |
| Apply writes partial files to S3                              | Low        | High   | Write to staging key first, then copy + delete; rollback on any failure; verify both objects present before marking file `cleaned`.                  |
| Cross-project leakage via guessed URLs                        | Medium     | Medium | All S3 ops scoped by project slug; presigned download URLs scoped per file with short TTL; called out as a known demo gap (real auth in production). |
| Schema auto-detection misclassifies columns                   | High       | Low    | Visible confirmation step with override modal; the user always sees what was detected before proceeding.                                             |
| Manifest concurrency conflict (two uploads racing)            | Low        | Medium | Single-user-per-project assumption holds for the demo; flagged as a production gap.                                                                  |
| Demo data feels too synthetic                                 | Medium     | Medium | Generator parameters tuned to plausible revenue-diligence patterns; canonical scenarios designed to feel like real target-company sales data.        |

---

## 10. What's not in the demo

Organized by the *reason* something isn't here. Each sub-section answers a different question.

### 10.1 Out of product scope

These don't fit the demo's product premise (single-analyst diligence cube cleaning). Building them would require a different product direction, not just more time.

- Multi-user collaboration on a single file
- File-level locking and concurrent editing
- Public API beyond what the frontend uses
- Bulk file processing (apply same rules to a folder)

### 10.2 Production hardening

Each bullet is a deliberate demo-time simplification — and the swap a real deployment would make. These aren't future-work tasks; they're the demo-to-production deltas.

- **Postgres** for state, replacing per-project `manifest.json` in S3.
- **OAuth/SSO + RBAC + multi-tenant isolation**, replacing the project-name namespace.
- **S3 Object Lock + KMS-managed encryption + retention policy** for the audit log, replacing S3 defaults.
- **APM + structured logging + alerting**, replacing stdout logs.
- **Idempotency tokens + optimistic concurrency** on manifest writes, replacing the single-user assumption.
- **CI quality gates + SAST + dependency scanning**, replacing auto-deploy-on-push.
- **Reconciliation checks at apply time** (total before == total after) — claimed in design, not enforced today.
- **`Decimal` / fixed-point money math**, replacing `float64`. Matters once cubes carry multi-currency or allocation-derived sub-cent values.
- **Memory guardrails + auto-triggered chunked Parquet reads** at 5M+ rows. The architecture is ready; the trigger logic is not.

### 10.3 Future work — what I'd build next

Listed roughly in priority order. The first five would be the natural next sprint; the rest are organized by area.

**Top priority:**

1. **Real auth** with `user_id` populated in the audit log (the schema field exists, just stubbed).
2. **CSV / Excel ingestion** behind the same upload contract.
3. **Saved review presets** ("accept all negatives under $X") reusable across files.
4. **Apply on a background job queue (Celery + Redis)** so large staged sets don't block the request thread.
5. **Outlier detector tuning** — learned per-column thresholds instead of per-row IQR.

**Detection improvements:**

- **Refund detection beyond row-local matching.** Today's detector flags a negative when the cumulative positive balance in the same row covers it, and the fix walks backward through that row absorbing from prior periods. Directions to grow it:
  - **Magnitude scoring with confidence** — bring back signals like round-number reversal (`|x|` ≥ 1,000 and divisible by 100) and MoM drop as inputs to a confidence score, calibrated against labeled refunds.
  - **Cross-row matching** — match a refund against sales for the same customer/product in *other* rows (e.g., refund booked under a different product line by mistake).
  - **Cross-file context** — reconcile cube negatives against an actual transactional / GL refunds export. The cube alone can't tell you which invoice a refund maps to.
  - **ML classifier** — once a labeled dataset exists, replace the heuristic with a trained model.
  - The architecture already carries a continuous `confidence` value through the API and audit log, so a richer detector slots in without schema changes.
- **Custom rules engine** (config-driven detectors).
- **ML-based outlier detection** (Isolation Forest, autoencoders).
- **Cross-file context** (reconcile refunds in `sales.parquet` against `gl-trial.parquet`).
- **Float-precision detection threshold.** The negatives detector uses strict `< 0` with no epsilon, so tiny floats like `-0.0000001` are flagged. Worth tuning if real data has sub-cent measurement noise.

**UX & workflow:**

- **Polished error UX across all stages.** Backend returns proper HTTP codes (400/404/409/500), and the Apply stage has a detailed error banner (network / 5xx / conflict states with retry). Missing: a global React error boundary, a dedicated 404 route for unknown project slugs and bad URLs, and consistent error states on the earlier stages (Upload / Schema / Detect / Review).
- **Audit log export as Excel** with embedded change history.
- **Review UI enhancements:** multi-select filter chips (Negatives + Outliers together), and sort controls (`$` magnitude, customer, period, confidence). Today filtering is single-select and order follows row index.
- **Invalidate staged selections on schema change.** Selections in `frontend/src/state/selections.ts` survive across stage navigation by design. If the user re-roles a measure column after staging, detection re-runs and old `detection_id`s go stale — selections should clear, and the `["detection-run", slug, fileId]` query should invalidate from SchemaStage's mutation success path. Pure navigation (no schema edit) should still preserve selections.
- **Re-detection on cleaned files.** The frontend stepper routes cleaned files to the completion view, but the backend `/detect` endpoint has no guard against re-running on an applied file. Decide: enable with audit trail, or block at API layer.
- **Undo / version history** for cleaned files (apply is one-way today).
- **Comments / notes per change** for analyst → VP handoff.

**Robustness:**

- **Edge-case data shapes.** Explicit handling for Unicode-heavy identifiers (RTL scripts, emoji, very long strings), very wide cubes (1,000+ time columns), mostly-empty cubes (95%+ zero density), and single-time-column degenerate cubes. These mostly work or fail silently today but aren't covered as explicit cases.

### 10.4 Implicit assumptions

Environmental constraints the demo accepts.

- Modern desktop browsers only (no mobile, no IE)
- English UI only
- Single currency per file
- Trusted file contents (no malicious-payload defense)
- One user per project at a time
- Confidence thresholds are heuristic, not statistically calibrated

---

## 11. Open questions

- Default action when a cell is flagged by multiple detectors with conflicting fixes — auto-select highest-priority detector, or force user choice?
- Audit log granularity for outlier flags (one entry per cell vs. row-level compression).

---

## 12. Glossary

- **Cube** — Wide-format aggregated table where rows are entity tuples (customer × product) and columns are time periods. The shape of our input data.
- **Sales cube** — A cube whose measure is revenue/sales. What this tool accepts.
- **Revenue diligence** — PE diligence work focused on understanding the target's top line: where revenue comes from, who from, what kind, is it durable. Distinct from balance-sheet diligence, working capital, etc.
- **PVM — Price/Volume/Mix** — Revenue-decomposition framework that splits period-over-period revenue change into price effect (Δprice × volume), volume effect (Δvolume × price), and mix effect (shift between products). Requires both revenue and units. Our sample cube is sales-side input only.
- **Revenue bridge** — Period-over-period revenue walk showing the drivers of change.
- **PE associate** — Junior investment professional at a private equity firm. Primary user.
- **IQR — Interquartile Range** — `Q3 − Q1`. Outlier rule: `[Q1 − 1.5·IQR, Q3 + 1.5·IQR]`.
- **Detector** — One of the four anomaly types: negatives, refunds, double-bookings, outliers.
- **Detection** — Output of a detector: `(row, column, suggested_fix, confidence)`.
- **Apply** — Commit selected fixes by writing cleaned parquet + audit log to S3.
- **Manifest** — Per-project JSON file in S3 listing files, statuses, metadata. The demo's "database."
