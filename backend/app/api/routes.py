"""REST routes — one thin wrapper per endpoint in DATA_MODEL.md.

Each route does the minimum glue: validate inputs, call into ``app.detect``
/ ``app.apply`` / ``app.storage``, return a Pydantic response.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import uuid

import orjson
from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import RequiredFile, RequiredProject, Storage
from app.api.schemas import (
    ApplyRequest,
    ApplyResponse,
    CoerceRequest,
    CreateUploadRequest,
    CreateUploadResponse,
    DetectionItem,
    DetectionsResponse,
    ManifestResponse,
    ParseRequest,
    PresignedUrlResponse,
    PreviewResponse,
    SchemaOverrideRequest,
    SchemaResponse,
)
from app.apply import Selection, apply_selections
from app.detect import Detection, detect_all
from app.detectors.base import AnomalyType
from app.schema import ColumnRoles, SchemaResult, infer_schema, validate_with_overrides
from app.storage.manifest import (
    add_file,
    get_or_create,
    read_manifest,
    remove_file,
    update_file,
)
from app.storage.s3 import DuplicateColumnsError

router = APIRouter(prefix="/api")


def _resolve_schema(df, file) -> SchemaResult:
    """Use the persisted override if the user saved one; otherwise auto-infer.

    Schema overrides (PATCH /schema) write to ``file.schema_``. Any endpoint
    that runs against the cube — detect, apply — must honor that override or
    it'll silently revert to the auto-detected roles. The first incarnation
    of this code only used overrides in GET /schema, which caused C3 to
    400 on detect: the user flipped `customer_id` to identifier in the modal,
    but the backend re-inferred and saw the same misclassification, then hit
    a duplicate-rows hard fail.
    """
    persisted = file.schema_ or {}
    if persisted.get("id_columns") or persisted.get("time_columns") or persisted.get("measure_columns"):
        return validate_with_overrides(
            df,
            ColumnRoles(
                id_columns=list(persisted.get("id_columns", [])),
                time_columns=list(persisted.get("time_columns", [])),
                measure_columns=list(persisted.get("measure_columns", [])),
            ),
        )
    return infer_schema(df)


def _to_schema_response(result: SchemaResult, df) -> SchemaResponse:
    """Common SchemaResponse builder. Every schema-shaped route returns the
    same fields — put the construction in one place so adding a field doesn't
    mean editing four routes."""
    return SchemaResponse(
        id_columns=result.roles.id_columns,
        time_columns=result.roles.time_columns,
        measure_columns=result.roles.measure_columns,
        all_columns=list(df.columns),
        time_format=result.time_format,
        hard_errors=result.hard_errors,
        soft_warnings=result.soft_warnings,
        coercible_columns=result.coercible_columns,
    )


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


@router.get("/projects/{slug}", response_model=ManifestResponse)
def get_project(slug: str, storage: Storage) -> ManifestResponse:
    # GET on a project slug acts as get-or-create — matches DATA_MODEL.md.
    m = read_manifest(storage, slug)
    if m is None:
        m = get_or_create(storage, slug)
    return ManifestResponse.model_validate(m.model_dump(by_alias=True))


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


@router.post("/projects/{slug}/files", response_model=CreateUploadResponse)
def create_upload(
    slug: str,
    body: CreateUploadRequest,
    storage: Storage,
    manifest: RequiredProject,
) -> CreateUploadResponse:
    """Issue a presigned PUT URL. The manifest entry is *not* written here —
    that happens in /parse, after the S3 PUT actually succeeds. Avoids
    orphaned manifest entries when the browser upload fails (CORS, network)."""
    if not body.filename.lower().endswith(".parquet"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Parquet files are supported in this demo.",
        )
    file_id = str(uuid.uuid4())
    key = storage.original_key(slug, file_id)
    url = storage.presigned_put(key, content_type=body.content_type)
    return CreateUploadResponse(
        file_id=file_id,
        upload_url=url,
        upload_headers={"Content-Type": body.content_type},
    )


@router.post("/projects/{slug}/files/{file_id}/parse", response_model=SchemaResponse)
def parse_file(
    slug: str,
    file_id: str,
    body: ParseRequest,
    storage: Storage,
    manifest: RequiredProject,
) -> SchemaResponse:
    """Confirm the upload landed, parse it, and *now* add the file to the
    manifest. If the S3 PUT failed (CORS, network, abandoned tab), this 400s
    and the manifest stays clean — no orphans."""
    key = storage.original_key(slug, file_id)
    if not storage.exists(key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload not complete — the uploaded file is missing.",
        )
    try:
        df = storage.get_parquet(key)
    except DuplicateColumnsError as exc:
        # SCH-15: duplicate column names.
        names = ", ".join(f"'{n}'" for n in exc.duplicates)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Your file has more than one column named {names}. "
                f"Please check the source file and re-upload."
            ),
        ) from exc
    except Exception as exc:
        # PyArrow throws on truncated / corrupted files. Friendly 400 +
        # delete the bad object so it doesn't linger in the bucket.
        with contextlib.suppress(Exception):
            storage.delete(key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to parse Parquet — the file is corrupted or not a valid Parquet.",
        ) from exc
    if len(df) == 0:
        with contextlib.suppress(Exception):
            storage.delete(key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File has no data rows.",
        )
    result = infer_schema(df)

    # Add to manifest only if upload + parse both succeeded.
    existing = manifest.file(file_id)
    if existing is None:
        add_file(storage, slug, body.filename, file_id=file_id, status="schema_pending")
    update_file(
        storage,
        slug,
        file_id,
        row_count=len(df),
        schema_={
            "id_columns": result.roles.id_columns,
            "time_columns": result.roles.time_columns,
            "measure_columns": result.roles.measure_columns,
            "time_format": result.time_format,
        },
        status="schema_pending",
    )
    # Prime the in-process cache so the user's next click (schema → detect)
    # doesn't pay the S3 download + parse cost again.
    if len(_DATAFRAME_CACHE) >= _DATAFRAME_CACHE_MAX:
        _DATAFRAME_CACHE.pop(next(iter(_DATAFRAME_CACHE)), None)
    _DATAFRAME_CACHE[(slug, file_id)] = df
    return _to_schema_response(result, df)


@router.delete("/projects/{slug}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(
    slug: str,
    file_id: str,
    storage: Storage,
    manifest: RequiredProject,
) -> None:
    """Drop a file from the manifest and best-effort delete its S3 objects.
    Idempotent — also wipes the S3 objects when the manifest entry is missing,
    in case of half-states."""
    if manifest.file(file_id) is not None:
        remove_file(storage, slug, file_id)
    for key_fn in (
        storage.original_key,
        storage.cleaned_key,
        storage.audit_key,
        storage.detections_key,
    ):
        with contextlib.suppress(Exception):
            storage.delete(key_fn(slug, file_id))
    _DETECTION_CACHE.pop(_detect_cache_key(slug, file_id), None)
    _ANOMALY_ROW_CACHE.pop(_detect_cache_key(slug, file_id), None)
    _invalidate_dataframe(slug, file_id)


@router.get("/projects/{slug}/files/{file_id}/schema", response_model=SchemaResponse)
def get_schema(
    slug: str,
    storage: Storage,
    file: RequiredFile,
) -> SchemaResponse:
    """Returns the persisted role assignment if the user previously saved
    overrides; otherwise auto-detects fresh. Warnings always re-run against
    the current data."""
    df = _get_dataframe(storage, slug, file.file_id)

    persisted = file.schema_ or {}
    if persisted.get("id_columns") or persisted.get("time_columns") or persisted.get("measure_columns"):
        result = validate_with_overrides(
            df,
            ColumnRoles(
                id_columns=list(persisted.get("id_columns", [])),
                time_columns=list(persisted.get("time_columns", [])),
                measure_columns=list(persisted.get("measure_columns", [])),
            ),
        )
        # Persisted time_columns are pre-sorted, so validate_with_overrides
        # can't detect the out-of-order condition. Re-check against the file's
        # actual on-disk column order instead.
        from app.schema import _detect_time_format, _is_time_named, _sort_time_columns
        file_time_cols = [c for c in df.columns if _is_time_named(c)]
        fmt, _ = _detect_time_format(file_time_cols)
        if (
            fmt
            and _sort_time_columns(file_time_cols, fmt) != file_time_cols
            and not any("chronological" in w for w in result.soft_warnings)
        ):
            result.soft_warnings.append(
                "Your time periods weren't in chronological order. We sorted them "
                "automatically so period-over-period checks work correctly. The source "
                "file is unchanged."
            )
    else:
        result = infer_schema(df)

    return _to_schema_response(result, df)


@router.patch("/projects/{slug}/files/{file_id}/schema", response_model=SchemaResponse)
def override_schema(
    slug: str,
    body: SchemaOverrideRequest,
    storage: Storage,
    file: RequiredFile,
) -> SchemaResponse:
    df = _get_dataframe(storage, slug, file.file_id)
    roles = ColumnRoles(
        id_columns=body.id_columns,
        time_columns=body.time_columns,
        measure_columns=body.measure_columns,
    )
    result = validate_with_overrides(df, roles)
    if result.ok:
        update_file(
            storage,
            slug,
            file.file_id,
            schema_={
                "id_columns": result.roles.id_columns,
                "time_columns": result.roles.time_columns,
                "measure_columns": result.roles.measure_columns,
                "time_format": result.time_format,
            },
        )
        # Schema change invalidates any persisted detection sidecar — the
        # detection_ids reference the previous role assignment.
        _invalidate_detection_sidecar(storage, slug, file.file_id)
    return _to_schema_response(result, df)


@router.post("/projects/{slug}/files/{file_id}/coerce", response_model=SchemaResponse)
def coerce_columns(
    slug: str,
    body: CoerceRequest,
    storage: Storage,
    file: RequiredFile,
) -> SchemaResponse:
    """Convert listed time-named string columns to numeric in-place. Non-numeric
    values become NaN. The Parquet is rewritten and the schema re-validated —
    coerced columns should now classify as measures.
    """
    import pandas as pd
    key = storage.original_key(slug, file.file_id)
    df = _get_dataframe(storage, slug, file.file_id)

    for col in body.columns:
        if col not in df.columns:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Column '{col}' not in file.",
            )
        df[col] = pd.to_numeric(df[col], errors="coerce")

    storage.put_parquet(key, df)
    # Cached frame was mutated in place — refresh the cache entry + drop
    # stale detections so /detect re-runs against the corrected columns.
    _DATAFRAME_CACHE[(slug, file.file_id)] = df
    _DETECTION_CACHE.pop(_detect_cache_key(slug, file.file_id), None)
    _ANOMALY_ROW_CACHE.pop(_detect_cache_key(slug, file.file_id), None)
    _invalidate_detection_sidecar(storage, slug, file.file_id)
    # Re-validate from scratch (auto-detect) — coerced columns should now
    # classify as measures.
    result = infer_schema(df)
    return _to_schema_response(result, df)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


# Per-file detection cache: detection lists can be a few MB, and the
# review UI fetches them repeatedly. Keyed by (slug, file_id). Cleared
# when the file is re-detected or apply runs.
_DETECTION_CACHE: dict[tuple[str, str], list] = {}

# Per-file parsed DataFrame cache. The same uploaded parquet is read by
# 4–5 endpoints during a typical workflow (schema → detect → preview →
# apply) — each call was previously paying the full S3 download + pandas
# parse cost. Stress.parquet (500k rows) is ~80MB on disk / ~1.5GB in
# memory, so repeating that work end-to-end is the dominant slowdown.
# Keyed by (slug, file_id). Capped at a few entries by FIFO eviction so
# the Fly machine doesn't OOM if many files are juggled. Cleared on
# delete + on schema override (override persists, but column ordering
# in the cached frame might not match the new role split).
_DATAFRAME_CACHE: dict[tuple[str, str], "pd.DataFrame"] = {}
_DATAFRAME_CACHE_MAX = 4


def _detect_cache_key(slug: str, file_id: str) -> tuple[str, str]:
    return (slug, file_id)


# Anomaly-row-index cache: maps (slug, file_id) → {"any": set[int],
# "negative": set[int], "refund": set[int], ...}. Populated alongside the
# detection list and used by /preview?detected=<type> to filter cube rows
# to only those that contain at least one detection of that type. Lets the
# review pane page through ~thousands of anomaly rows on stress instead of
# scrolling past 500k mostly-clean rows. Always rebuilt from the detection
# list, so it stays consistent with the cache it sits next to.
_ANOMALY_ROW_CACHE: dict[tuple[str, str], dict[str, set[int]]] = {}


def _put_audit_bytes(storage, key: str, body: bytes) -> None:
    """Upload gzipped audit JSON with the headers a browser needs to
    transparently decompress on download. ``Content-Encoding: gzip`` makes
    the browser uncompress automatically when the user fetches via the
    presigned URL; ``Content-Type: application/json`` keeps the .json
    extension semantically correct (the wire bytes are gzipped but the
    decoded resource is JSON). Without ``Content-Encoding`` the user would
    download a raw gzipped blob that file viewers can't open as JSON."""
    storage.client.put_object(
        Bucket=storage.bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
        ContentEncoding="gzip",
    )


def _copy_object(storage, src_key: str, dst_key: str) -> None:
    """S3 server-side copy. Used by /apply to promote staged objects to
    final keys without re-uploading the bytes from the worker. Saves the
    cost (and memory peak) of holding the serialized blobs twice when
    the cleaned parquet + audit log are both hundreds of megabytes."""
    storage.client.copy_object(
        Bucket=storage.bucket,
        CopySource={"Bucket": storage.bucket, "Key": src_key},
        Key=dst_key,
    )


def _invalidate_detection_sidecar(storage, slug: str, file_id: str) -> None:
    """Drop the persisted detections.json from S3 (best-effort). Called
    when schema overrides or coerce mutate the underlying inputs — the
    cached detection_ids would otherwise reference a stale view of the
    cube. Also wipes the in-process caches so the next /detect recomputes."""
    with contextlib.suppress(Exception):
        storage.delete(storage.detections_key(slug, file_id))
    _DETECTION_CACHE.pop(_detect_cache_key(slug, file_id), None)
    _ANOMALY_ROW_CACHE.pop(_detect_cache_key(slug, file_id), None)


# Bump this string whenever detector logic changes in a way that affects the
# detection contract (suggested_fix priorities, new fields, schema). Sidecars
# written under an older version are rejected by ``_load_detections``, which
# forces a fresh ``/detect`` on next read. Keep the format compact — wrapped
# as ``{"version": "...", "detections": [...]}`` instead of a bare list.
_DETECTIONS_FORMAT_VERSION = "v2"


def _persist_detections(storage, slug: str, file_id: str, dets: list[Detection]) -> None:
    """Write the detection list to S3 as a versioned sidecar. Survives Fly
    machine restarts and lets a user revisiting the same file skip the
    detection compute entirely."""
    payload = json.dumps({
        "version": _DETECTIONS_FORMAT_VERSION,
        "detections": [_detection_to_jsonable(d) for d in dets],
    }).encode()
    storage.put_bytes(
        storage.detections_key(slug, file_id), payload, content_type="application/json",
    )


def _load_detections(storage, slug: str, file_id: str) -> list[Detection] | None:
    """Load a previously-persisted detection list. Returns None on cache
    miss (key doesn't exist), version mismatch (older format), or any
    deserialization issue — caller falls back to recomputing.

    The version check matters: a sidecar written before a detector logic
    change (e.g., the DBL/Outlier priority fix) would otherwise serve
    stale ``suggested_fix`` values, even though the deployed code is new."""
    key = storage.detections_key(slug, file_id)
    if not storage.exists(key):
        return None
    try:
        body = json.loads(storage.get_bytes(key))
        # Bare list = legacy v1 format. Reject.
        if not isinstance(body, dict):
            return None
        if body.get("version") != _DETECTIONS_FORMAT_VERSION:
            return None
        items = body.get("detections") or []
        return [_detection_from_jsonable(item) for item in items]
    except Exception:
        return None


def _detection_to_jsonable(d: Detection) -> dict:
    return {
        "detection_id": d.detection_id,
        "row_idx": d.row_idx,
        "row_key": d.row_key,
        "column": d.column,
        "value": d.value,
        "flagged_by": d.flagged_by,
        "suggested_fix": d.suggested_fix,
        "confidence": d.confidence,
        "alternative_fixes": list(d.alternative_fixes),
    }


def _detection_from_jsonable(item: dict) -> Detection:
    return Detection(
        detection_id=item["detection_id"],
        row_idx=item["row_idx"],
        row_key=item["row_key"],
        column=item["column"],
        value=item["value"],
        flagged_by=list(item["flagged_by"]),
        suggested_fix=item["suggested_fix"],
        confidence=item["confidence"],
        alternative_fixes=list(item.get("alternative_fixes", [])),
    )


def _build_anomaly_row_sets(dets) -> dict[str, set[int]]:
    """Index detections by detector type. ``any`` is the union — used by
    the default /preview?detected=1 (no specific type)."""
    sets: dict[str, set[int]] = {
        "any": set(),
        "negative": set(),
        "refund": set(),
        "double_booking": set(),
        "outlier": set(),
    }
    for d in dets:
        if d.row_idx is None:
            continue
        sets["any"].add(d.row_idx)
        for t in d.flagged_by:
            sets[t].add(d.row_idx)
    return sets


def _get_dataframe(storage, slug: str, file_id: str) -> "pd.DataFrame":
    """Return the parsed DataFrame for this file, reading + caching on miss.

    The cached frame is the file's original cube — never the cleaned copy.
    Mutations in the apply path operate on a deep copy, so the cache stays
    safe to hand out to subsequent requests.
    """
    key = (slug, file_id)
    cached = _DATAFRAME_CACHE.get(key)
    if cached is not None:
        return cached
    df = storage.get_parquet(storage.original_key(slug, file_id))
    if len(_DATAFRAME_CACHE) >= _DATAFRAME_CACHE_MAX:
        # FIFO eviction — drop the oldest. Stress files (~1.5GB in memory)
        # mean we can't keep many around.
        oldest = next(iter(_DATAFRAME_CACHE))
        _DATAFRAME_CACHE.pop(oldest, None)
    _DATAFRAME_CACHE[key] = df
    return df


def _invalidate_dataframe(slug: str, file_id: str) -> None:
    _DATAFRAME_CACHE.pop((slug, file_id), None)


@router.post("/projects/{slug}/files/{file_id}/detect", response_model=DetectionsResponse)
def run_detection(
    slug: str,
    storage: Storage,
    file: RequiredFile,
) -> DetectionsResponse:
    """Run all four detectors and return the merged detection list.

    Resolution order: in-process cache → S3 sidecar → compute. The S3
    sidecar (``detections.json``) makes revisits to a previously-detected
    file instant even after a Fly machine restart. Cache layers stay
    populated whenever detections are loaded, so the anomaly-row index
    used by /preview filtering is always available.
    """
    key = _detect_cache_key(slug, file.file_id)

    dets = _DETECTION_CACHE.get(key)
    if dets is None:
        persisted = _load_detections(storage, slug, file.file_id)
        if persisted is not None:
            dets = persisted

    if dets is None:
        df = _get_dataframe(storage, slug, file.file_id)
        sch = _resolve_schema(df, file)
        if not sch.ok:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Schema errors block detection: {sch.hard_errors}",
            )
        dets = detect_all(df, sch.roles.id_columns, sch.roles.measure_columns)
        _persist_detections(storage, slug, file.file_id, dets)

    _DETECTION_CACHE[key] = dets
    _ANOMALY_ROW_CACHE[key] = _build_anomaly_row_sets(dets)

    counts = _count_by_detector(dets)
    update_file(
        storage,
        slug,
        file.file_id,
        status="detected",
        anomaly_counts=counts,
    )
    return _to_detections_response(dets, counts)


@router.get("/projects/{slug}/files/{file_id}/detections", response_model=DetectionsResponse)
def list_detections(
    slug: str,
    storage: Storage,
    file: RequiredFile,
) -> DetectionsResponse:
    """Return the detection list for a file. Resolution order matches
    ``/detect`` and ``/apply``: in-process cache → S3 sidecar → recompute.

    Critically, the S3 sidecar is checked *before* recomputing. ``detection_id``s
    are counter-based per ``detect_all`` run, so recomputing here after a Fly
    machine restart would return fresh IDs that don't match what the frontend
    has staged — every subsequent ``/apply`` selection would 400 with
    "Unknown detection_id". The sidecar preserves the exact ID list.
    """
    key = _detect_cache_key(slug, file.file_id)
    dets = _DETECTION_CACHE.get(key)
    if dets is None:
        dets = _load_detections(storage, slug, file.file_id)
    if dets is None:
        # Sidecar also missing — true cold start. Recompute and persist so
        # the IDs are stable for subsequent calls.
        df = _get_dataframe(storage, slug, file.file_id)
        sch = _resolve_schema(df, file)
        dets = detect_all(df, sch.roles.id_columns, sch.roles.measure_columns)
        _persist_detections(storage, slug, file.file_id, dets)
    _DETECTION_CACHE[key] = dets
    _ANOMALY_ROW_CACHE[key] = _build_anomaly_row_sets(dets)
    return _to_detections_response(dets, _count_by_detector(dets))


# ---------------------------------------------------------------------------
# Preview (cube slice for the review UI's spreadsheet view)
# ---------------------------------------------------------------------------


@router.get("/projects/{slug}/files/{file_id}/preview", response_model=PreviewResponse)
def get_preview(
    slug: str,
    storage: Storage,
    file: RequiredFile,
    offset: int = 0,
    limit: int = 100,
    detected: str | None = None,
) -> PreviewResponse:
    """Paginated cube slice.

    ``detected`` filters to rows containing at least one detection. Values:
    - omitted / ``None`` → all rows (default)
    - ``any`` → rows flagged by any detector
    - ``negative`` / ``refund`` / ``double_booking`` / ``outlier`` → rows
      flagged by that specific detector

    The filter uses an anomaly-row-index set built at /detect time, so each
    /preview call is just a sorted slice over the pre-computed index. Total
    reflects the filtered population so the frontend can render a correct
    scrollbar.
    """
    df = _get_dataframe(storage, slug, file.file_id)
    columns = list(df.columns)
    if detected:
        key = _detect_cache_key(slug, file.file_id)
        row_sets = _ANOMALY_ROW_CACHE.get(key)
        if row_sets is None:
            # Cache miss after restart. Try to heal from the persisted
            # detection sidecar so a fully-detected file doesn't appear to
            # need re-detection on a cold machine.
            persisted = _load_detections(storage, slug, file.file_id)
            if persisted is not None:
                _DETECTION_CACHE[key] = persisted
                row_sets = _build_anomaly_row_sets(persisted)
                _ANOMALY_ROW_CACHE[key] = row_sets
        if row_sets is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Detection has not been run for this file yet.",
            )
        idxs = row_sets.get(detected)
        if idxs is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown detector type '{detected}'.",
            )
        ordered = sorted(idxs)
        total = len(ordered)
        page_idxs = ordered[offset : offset + limit]
        window = df.iloc[page_idxs]
        row_indices = list(page_idxs)
    else:
        total = len(df)
        end = min(offset + limit, total)
        window = df.iloc[offset:end]
        row_indices = list(range(offset, end))
    rows = window.to_dict(orient="records")
    return PreviewResponse(
        rows=rows,
        row_indices=row_indices,
        columns=columns,
        cursor=str(offset + limit) if offset + limit < total else None,
        total=total,
    )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


@router.post("/projects/{slug}/files/{file_id}/apply", response_model=ApplyResponse)
def apply(
    slug: str,
    body: ApplyRequest,
    storage: Storage,
    file: RequiredFile,
) -> ApplyResponse:
    cleaned_key = storage.cleaned_key(slug, file.file_id)
    audit_key = storage.audit_key(slug, file.file_id)
    if storage.exists(cleaned_key):
        # APP-04: idempotent. Don't re-apply.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="File already cleaned. Download the cleaned file and audit log instead.",
        )

    original_key = storage.original_key(slug, file.file_id)
    if not storage.exists(original_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Original file missing in S3.",
        )

    df = _get_dataframe(storage, slug, file.file_id)
    sch = _resolve_schema(df, file)
    if not sch.ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Schema errors block apply: {sch.hard_errors}",
        )

    # Need the in-memory detections to resolve selection IDs to cells.
    # Order: in-process cache → S3 sidecar → recompute. Critical to check the
    # sidecar before recomputing — detection_ids are counter-based per /detect
    # run, so a fresh recompute would generate new IDs that don't match what
    # the frontend has staged (every selection would 400 with "Unknown
    # detection_id"). This is exactly what happens after a Fly deploy or OOM
    # restart wipes the in-process cache.
    key = _detect_cache_key(slug, file.file_id)
    dets = _DETECTION_CACHE.get(key)
    if dets is None:
        dets = _load_detections(storage, slug, file.file_id)
        if dets is not None:
            _DETECTION_CACHE[key] = dets
    if dets is None:
        dets = detect_all(df, sch.roles.id_columns, sch.roles.measure_columns)
        _DETECTION_CACHE[key] = dets
        _persist_detections(storage, slug, file.file_id, dets)

    # Free the anomaly-row index for this file before doing the heavy lift —
    # apply doesn't need it and on stress.parquet it's ~100 MB. Cheap memory
    # win that helps fit large staged sets under the VM's RAM ceiling.
    _ANOMALY_ROW_CACHE.pop(key, None)

    selections = [
        Selection(s.detection_id, s.fix, attribution=s.attribution)
        for s in body.selections
    ]
    try:
        result = apply_selections(
            df,
            dets,
            selections,
            file_id=file.file_id,
            measure_columns=sch.roles.measure_columns,
            project_slug=slug,
        )
    except Exception as exc:  # InvalidSelectionError or other
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # Atomic-ish write: stage both objects first, then promote via S3
    # server-side copy. Compared to the original "PUT twice" approach this
    # serializes the audit JSON once (saving ~500 MB peak on stress where
    # the audit list is ~half a GB) and re-uses the S3-side bytes for the
    # final promotion (no second upload). If the audit stage fails, the
    # cleaned stage is deleted so a retry sees a clean slate.
    # Capture summary stats up front; we'll drop ``result`` early to free
    # the large cleaned_df + audit list before the S3 server-side promote.
    applied_at_iso = result.audit["applied_at"]
    audit_summary = result.audit["summary"]
    total_changes = len(result.audit["changes"])

    staging_cleaned = f"{cleaned_key}.staging-{uuid.uuid4().hex[:8]}"
    staging_audit = f"{audit_key}.staging-{uuid.uuid4().hex[:8]}"
    # orjson is ~3× faster than stdlib json on large dict lists and produces
    # bytes directly (no extra encode step). gzip cuts the wire payload
    # ~10× on stress.parquet audit logs (~500 MB → ~50 MB) because the
    # entries share many repeated keys / floats. The Content-Encoding
    # header tells browsers to transparently decompress when the user
    # downloads via the presigned URL, so the cleaned-up file looks
    # identical to the old uncompressed audit.json.
    audit_bytes = gzip.compress(
        orjson.dumps(result.audit, option=orjson.OPT_INDENT_2),
        compresslevel=6,
    )
    try:
        storage.put_parquet(staging_cleaned, result.cleaned_df)
        _put_audit_bytes(storage, staging_audit, audit_bytes)
        # Free local Python references to large objects before the promote —
        # cleaned_df is ~136 MB, audit dict is ~550 MB on stress.
        del result, audit_bytes
        _copy_object(storage, staging_cleaned, cleaned_key)
        _copy_object(storage, staging_audit, audit_key)
    finally:
        # Always clean up staging keys, success or fail.
        for k in (staging_cleaned, staging_audit):
            with contextlib.suppress(Exception):
                storage.delete(k)

    update_file(
        storage,
        slug,
        file.file_id,
        status="cleaned",
        applied_changes=total_changes,
        cleaned_at=applied_at_iso,
    )

    return ApplyResponse(
        file_id=file.file_id,
        applied_at=applied_at_iso,
        summary=audit_summary,
        total_changes=total_changes,
        cleaned_url=f"/api/projects/{slug}/files/{file.file_id}/cleaned-url",
        audit_url=f"/api/projects/{slug}/files/{file.file_id}/audit-url",
    )


# ---------------------------------------------------------------------------
# Presigned downloads
# ---------------------------------------------------------------------------


def _download_basename(original_filename: str) -> str:
    """Strip the .parquet extension from the source filename so we can
    append a suffix like '__cleaned.parquet' / '__audit.json' for the
    downloaded artifacts."""
    name = original_filename
    if name.lower().endswith(".parquet"):
        name = name[: -len(".parquet")]
    return name


@router.get("/projects/{slug}/files/{file_id}/cleaned-url", response_model=PresignedUrlResponse)
def cleaned_url(
    slug: str,
    storage: Storage,
    file: RequiredFile,
) -> PresignedUrlResponse:
    key = storage.cleaned_key(slug, file.file_id)
    if not storage.exists(key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cleaned file not produced yet.")
    base = _download_basename(file.original_filename)
    return PresignedUrlResponse(
        url=storage.presigned_get(key, download_filename=f"{base}__cleaned.parquet"),
        expires_in=storage.settings.presigned_url_ttl,
    )


@router.get("/projects/{slug}/files/{file_id}/audit-url", response_model=PresignedUrlResponse)
def audit_url(
    slug: str,
    storage: Storage,
    file: RequiredFile,
) -> PresignedUrlResponse:
    key = storage.audit_key(slug, file.file_id)
    if not storage.exists(key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit log not produced yet.")
    base = _download_basename(file.original_filename)
    return PresignedUrlResponse(
        url=storage.presigned_get(key, download_filename=f"{base}__audit.json"),
        expires_in=storage.settings.presigned_url_ttl,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_by_detector(dets) -> dict[str, int]:
    counts: dict[AnomalyType, int] = {
        "negative": 0, "refund": 0, "double_booking": 0, "outlier": 0,
    }
    for d in dets:
        for t in d.flagged_by:
            counts[t] += 1
    return counts


def _to_detections_response(dets, counts: dict[str, int]) -> DetectionsResponse:
    items = [
        DetectionItem(
            detection_id=d.detection_id,
            row_idx=d.row_idx,
            row_key=d.row_key,
            column=d.column,
            value=d.value,
            flagged_by=d.flagged_by,
            suggested_fix=d.suggested_fix,
            confidence=d.confidence,
            alternative_fixes=d.alternative_fixes,
        )
        for d in dets
    ]
    return DetectionsResponse(
        detections=items,
        cursor=None,
        total=len(items),
        counts=counts,
    )
