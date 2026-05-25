"""REST routes — one thin wrapper per endpoint in DATA_MODEL.md.

Each route does the minimum glue: validate inputs, call into ``app.detect``
/ ``app.apply`` / ``app.storage``, return a Pydantic response.
"""

from __future__ import annotations

import contextlib
import json
import uuid

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
from app.detect import detect_all
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
    for key_fn in (storage.original_key, storage.cleaned_key, storage.audit_key):
        with contextlib.suppress(Exception):
            storage.delete(key_fn(slug, file_id))
    _DETECTION_CACHE.pop(_detect_cache_key(slug, file_id), None)


@router.get("/projects/{slug}/files/{file_id}/schema", response_model=SchemaResponse)
def get_schema(
    slug: str,
    storage: Storage,
    file: RequiredFile,
) -> SchemaResponse:
    """Returns the persisted role assignment if the user previously saved
    overrides; otherwise auto-detects fresh. Warnings always re-run against
    the current data."""
    df = storage.get_parquet(storage.original_key(slug, file.file_id))

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
    df = storage.get_parquet(storage.original_key(slug, file.file_id))
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
    df = storage.get_parquet(key)

    for col in body.columns:
        if col not in df.columns:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Column '{col}' not in file.",
            )
        df[col] = pd.to_numeric(df[col], errors="coerce")

    storage.put_parquet(key, df)
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


def _detect_cache_key(slug: str, file_id: str) -> tuple[str, str]:
    return (slug, file_id)


@router.post("/projects/{slug}/files/{file_id}/detect", response_model=DetectionsResponse)
def run_detection(
    slug: str,
    storage: Storage,
    file: RequiredFile,
) -> DetectionsResponse:
    df = storage.get_parquet(storage.original_key(slug, file.file_id))
    sch = infer_schema(df)
    if not sch.ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Schema errors block detection: {sch.hard_errors}",
        )
    dets = detect_all(df, sch.roles.id_columns, sch.roles.measure_columns)
    _DETECTION_CACHE[_detect_cache_key(slug, file.file_id)] = dets

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
    dets = _DETECTION_CACHE.get(_detect_cache_key(slug, file.file_id))
    if dets is None:
        # Cache miss — re-run detection (e.g., backend restarted between
        # /detect and /detections). Cheap relative to typical demo size.
        df = storage.get_parquet(storage.original_key(slug, file.file_id))
        sch = infer_schema(df)
        dets = detect_all(df, sch.roles.id_columns, sch.roles.measure_columns)
        _DETECTION_CACHE[_detect_cache_key(slug, file.file_id)] = dets
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
    limit: int = 50,
) -> PreviewResponse:
    df = storage.get_parquet(storage.original_key(slug, file.file_id))
    window = df.iloc[offset : offset + limit]
    rows = window.to_dict(orient="records")
    return PreviewResponse(
        rows=rows,
        columns=list(df.columns),
        cursor=str(offset + limit) if offset + limit < len(df) else None,
        total=len(df),
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

    df = storage.get_parquet(original_key)
    sch = infer_schema(df)
    if not sch.ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Schema errors block apply: {sch.hard_errors}",
        )

    # Need the in-memory detections to resolve selection IDs to cells.
    dets = _DETECTION_CACHE.get(_detect_cache_key(slug, file.file_id))
    if dets is None:
        dets = detect_all(df, sch.roles.id_columns, sch.roles.measure_columns)
        _DETECTION_CACHE[_detect_cache_key(slug, file.file_id)] = dets

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

    # Write to staging keys first, then commit by moving to final keys. This
    # gets us atomic-ish behavior — neither final object exists unless both
    # staging writes succeeded. Two-PUT failure mode shown in APP-03.
    staging_cleaned = f"{cleaned_key}.staging-{uuid.uuid4().hex[:8]}"
    staging_audit = f"{audit_key}.staging-{uuid.uuid4().hex[:8]}"
    try:
        storage.put_parquet(staging_cleaned, result.cleaned_df)
        storage.put_bytes(
            staging_audit,
            json.dumps(result.audit, indent=2).encode("utf-8"),
            content_type="application/json",
        )
        # Both staging writes succeeded — promote.
        storage.put_parquet(cleaned_key, result.cleaned_df)
        storage.put_bytes(
            audit_key,
            json.dumps(result.audit, indent=2).encode("utf-8"),
            content_type="application/json",
        )
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
        applied_changes=len(result.audit["changes"]),
        cleaned_at=result.audit["applied_at"],
    )

    return ApplyResponse(
        file_id=file.file_id,
        applied_at=result.audit["applied_at"],
        summary=result.audit["summary"],
        total_changes=len(result.audit["changes"]),
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
