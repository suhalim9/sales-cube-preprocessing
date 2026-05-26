"""Pydantic request/response schemas for the REST API.

Wire shapes mirror ``DATA_MODEL.md``. Kept here (not in ``app.detect`` or
``app.apply``) so the internal layer can evolve without breaking API
consumers.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.detectors.base import AnomalyType, SuggestedFix

# ---------------------------------------------------------------------------
# Projects / files
# ---------------------------------------------------------------------------


class FileSummary(BaseModel):
    file_id: str
    original_filename: str
    uploaded_at: str
    row_count: int
    status: str
    anomaly_counts: dict[str, int] | None = None
    applied_changes: int | None = None
    cleaned_at: str | None = None


class ManifestResponse(BaseModel):
    project_name: str
    project_slug: str
    created_at: str
    updated_at: str
    files: list[FileSummary]


class CreateUploadResponse(BaseModel):
    file_id: str
    upload_url: str
    upload_headers: dict[str, str]


class CreateUploadRequest(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"


class ParseRequest(BaseModel):
    filename: str


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class SchemaResponse(BaseModel):
    id_columns: list[str]
    time_columns: list[str]
    measure_columns: list[str]
    # Every column in the source file, in original order. The override UI
    # uses this so excluded columns can be re-assigned later — they aren't
    # in any of the role lists once excluded.
    all_columns: list[str] = []
    time_format: str | None
    hard_errors: list[str]
    soft_warnings: list[str]
    # Time-named columns that need a tiny coerce (a handful of non-numeric
    # strings) to be usable as measures. UI offers a one-click action.
    coercible_columns: list[str] = []


class CoerceRequest(BaseModel):
    columns: list[str]


class SchemaOverrideRequest(BaseModel):
    id_columns: list[str]
    time_columns: list[str]
    measure_columns: list[str]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


class DetectionItem(BaseModel):
    detection_id: str
    row_idx: int
    row_key: dict[str, Any]
    column: str
    value: float
    flagged_by: list[AnomalyType]
    suggested_fix: SuggestedFix
    confidence: float
    alternative_fixes: list[SuggestedFix]


class DetectionsResponse(BaseModel):
    detections: list[DetectionItem]
    cursor: str | None = None
    total: int
    counts: dict[AnomalyType, int]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


class SelectionInput(BaseModel):
    detection_id: str
    fix: SuggestedFix
    # Detector the user staged from (active left-rail tab). Drives attribution
    # in the audit log and the refund-specific apply behavior. None means the
    # caller didn't pick a context — apply falls back to priority order.
    attribution: AnomalyType | None = None


class ApplyRequest(BaseModel):
    selections: list[SelectionInput] = Field(default_factory=list)


class ApplyResponse(BaseModel):
    file_id: str
    applied_at: str
    summary: dict[str, int]
    total_changes: int
    cleaned_url: str
    audit_url: str


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


class PreviewResponse(BaseModel):
    rows: list[dict[str, Any]]
    # Original cube row index for each entry in ``rows``. Needed because
    # ``/preview?detected=<type>`` returns a sparse subset (e.g. rows 17,
    # 42, 103), so the array position can't be used as the cube row index
    # — but detections are keyed by it.
    row_indices: list[int]
    columns: list[str]
    cursor: str | None = None
    total: int


# ---------------------------------------------------------------------------
# Presigned download
# ---------------------------------------------------------------------------


class PresignedUrlResponse(BaseModel):
    url: str
    expires_in: int


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None
