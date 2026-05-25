# Data Model & API Contract

Reference doc for the data cleaning demo. Covers S3 layout, the per-project manifest, the audit log, and the REST API surface. Sourced from decisions in [`ROADMAP.md`](./ROADMAP.md) and [`ASSUMPTIONS.md`](./ASSUMPTIONS.md).

---

## S3 layout

```
s3://{bucket}/
└── projects/
    └── {project-slug}/
        ├── manifest.json
        └── files/
            └── {file-id}/
                ├── original.parquet
                ├── cleaned.parquet
                └── audit.json
```

- `{project-slug}` — kebab-case derived from the user-entered project name. Collisions append a numeric suffix.
- `{file-id}` — server-generated UUID v4.
- `original.parquet` is immutable once written.
- `cleaned.parquet` and `audit.json` are written together by the apply path; either both exist or neither does.

---

## Manifest schema (`manifest.json`)

One per project. Source of truth for the project's file list, statuses, and metadata. Written on every state transition (file upload, schema confirm, detect complete, apply complete).

```json
{
  "project_name": "Acme Diligence Q1",
  "project_slug": "acme-diligence-q1",
  "created_at": "2025-05-21T09:00:00Z",
  "updated_at": "2025-05-23T10:32:11Z",
  "files": [
    {
      "file_id": "8f4c2a1e-...",
      "original_filename": "sales.parquet",
      "uploaded_at": "2025-05-21T09:05:00Z",
      "row_count": 487000,
      "schema": {
        "id_columns": ["customer", "product_line"],
        "time_columns": ["2021_1", "2021_2", "..."],
        "measure_columns": ["2021_1", "2021_2", "..."]
      },
      "status": "cleaned",
      "anomaly_counts": {
        "negative": 47,
        "refund": 12,
        "double_booking": 8,
        "outlier": 15
      },
      "applied_changes": 42,
      "cleaned_at": "2025-05-23T10:32:11Z"
    }
  ]
}
```

**Status values:**
- `uploaded` — file in S3, not yet parsed
- `schema_pending` — parsed, waiting for user to confirm column roles
- `detected` — detection complete, ready for review
- `cleaning` — review in progress (selections staged)
- `cleaned` — apply committed, downloadable

---

## Audit log schema (`audit.json`)

Written alongside `cleaned.parquet` on apply. One entry per applied change, plus a summary header. Immutable in spirit (real production would enforce via S3 Object Lock).

```json
{
  "file_id": "8f4c2a1e-...",
  "applied_at": "2025-05-23T10:32:11Z",
  "user_id": "project:acme-diligence-q1",
  "summary": {
    "negative": 21,
    "refund": 8,
    "double_booking": 5,
    "outlier": 12
  },
  "changes": [
    {
      "change_id": "uuid",
      "anomaly_type": "negative",
      "row_key": {
        "customer": "Cust_3",
        "product_line": "Product_B"
      },
      "column": "2022_5",
      "value_before": -1247.0,
      "value_after": 0.0,
      "suggested_fix": "set_to_zero",
      "flagged": false
    },
    {
      "change_id": "uuid",
      "anomaly_type": "outlier",
      "row_key": {
        "customer": "Cust_12",
        "product_line": "Product_C"
      },
      "column": "2022_5",
      "value_before": -50000.0,
      "value_after": -50000.0,
      "suggested_fix": "keep_as_is",
      "flagged": true
    }
  ]
}
```

**Fields:**
- `anomaly_type` ∈ `"negative" | "refund" | "double_booking" | "outlier"` — which detector this change is attributed to.
- `suggested_fix` ∈ `"set_to_zero" | "split_evenly" | "keep_as_is"`.
- `flagged: true` indicates a no-value-change entry (`keep_as_is`). `value_before == value_after` in this case.
- `change_id` is unique within a single audit log.

For double-booking `split_evenly`, the audit emits **two** change entries (one for each affected cell, `value_before == X` and `value_after == X/2`). For refund `set_to_zero`, the audit emits one entry for the refund cell plus one entry per prior period absorbed during the FIFO walk-back (see ROADMAP §6 Refunds).

---

## Detection contract

Shape of detection results returned by `GET /detections`:

```json
{
  "detections": [
    {
      "detection_id": "uuid",
      "row_key": {
        "customer": "Cust_3",
        "product_line": "Product_B"
      },
      "column": "2022_5",
      "value": -1247.0,
      "flagged_by": ["negative", "refund"],
      "suggested_fix": "set_to_zero",
      "confidence": 1.0,
      "alternative_fixes": []
    }
  ],
  "cursor": "next-page-token-or-null",
  "total": 82
}
```

- `flagged_by` is an array — multiple detectors can flag the same cell.
- `confidence` is in `[0, 1]`; only varies for outliers (the IQR-based score). Negatives, refunds, and double-bookings are boolean and return `1.0`.
- `alternative_fixes` lists options when the detector has more than one suggested treatment (outliers: `["set_to_zero"]` as an alternative to the default `keep_as_is`).

---

## REST API

Base path: `/api`. All routes scoped to a `{project_slug}`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/projects/{slug}` | Get project manifest (creates the project on first call) |
| `POST` | `/projects/{slug}/files` | Initiate file upload; returns S3 presigned upload URL + `file_id` |
| `POST` | `/projects/{slug}/files/{file_id}/parse` | Trigger parse + schema inference after upload completes |
| `GET` | `/projects/{slug}/files/{file_id}/schema` | Get detected schema |
| `PATCH` | `/projects/{slug}/files/{file_id}/schema` | Override role assignments |
| `POST` | `/projects/{slug}/files/{file_id}/detect` | Run all detectors |
| `GET` | `/projects/{slug}/files/{file_id}/detections` | Paginated detections; query params: `filter`, `sort`, `cursor`, `limit` |
| `POST` | `/projects/{slug}/files/{file_id}/apply` | Commit selected fixes (atomic) |
| `GET` | `/projects/{slug}/files/{file_id}/cleaned-url` | Presigned URL for `cleaned.parquet` |
| `GET` | `/projects/{slug}/files/{file_id}/audit-url` | Presigned URL for `audit.json` |
| `GET` | `/projects/{slug}/files/{file_id}/preview` | Paginated cube cells for the "View data" drawer |

### `POST /apply` body

```json
{
  "selections": [
    {
      "detection_id": "uuid",
      "fix": "set_to_zero"
    },
    {
      "detection_id": "uuid",
      "fix": "split_evenly"
    }
  ]
}
```

For overlapping cells (one `(row_key, column)` referenced by multiple detection IDs), the server picks the chosen fix; if multiple selections agree on the same fix, it's idempotent; if they conflict, the server returns 409 with the conflicting detection IDs so the client can prompt for resolution.

### Pagination contract

All list endpoints (`/detections`, `/preview`) use cursor pagination:
- Request: `?cursor=<opaque>&limit=50`
- Response: includes `cursor` (next page token or `null` when done) and `total` (count under current filter).

This is the mechanism that lets the architecture scale from 500k to 5M rows without UI rework — the frontend never receives more than `limit` rows at a time.

---

## Notes

- All timestamps are ISO 8601 UTC.
- All monetary values are 64-bit floats. Not appropriate for accounting-grade precision (see implicit assumptions in `ROADMAP.md` §10).
- `change_id` and `detection_id` are UUID v4.
- `user_id` in the audit log is a placeholder — currently always `"project:{slug}"`. Real auth would populate this with the authenticated user's ID.
