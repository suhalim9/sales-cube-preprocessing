"""Per-project manifest read/write.

The manifest is the single source of truth for a project's file list and
statuses (no Postgres in this demo). Reads check ``S3 exists`` first to
distinguish "project not found" from "manifest missing". Writes refresh
``updated_at`` and replace the whole object atomically (S3 PUT is atomic
per-key).

Concurrency note: we don't implement optimistic locking or version
checks. Per ASSUMPTIONS.md, the demo is single-user-per-project.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.storage.s3 import S3Storage

FileStatus = Literal[
    "uploaded", "schema_pending", "detected", "cleaning", "cleaned"
]


class ManifestFile(BaseModel):
    file_id: str
    original_filename: str
    uploaded_at: str
    row_count: int = 0
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    status: FileStatus = "uploaded"
    anomaly_counts: dict[str, int] | None = None
    applied_changes: int | None = None
    cleaned_at: str | None = None

    model_config = {"populate_by_name": True}


class Manifest(BaseModel):
    project_name: str
    project_slug: str
    created_at: str
    updated_at: str
    files: list[ManifestFile] = Field(default_factory=list)

    def file(self, file_id: str) -> ManifestFile | None:
        return next((f for f in self.files if f.file_id == file_id), None)


def slugify(name: str) -> str:
    """kebab-case, ASCII-folded slug used as the S3 namespace."""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"^-+|-+$", "", s)
    return s[:64] or "untitled"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_manifest(project_name: str, project_slug: str | None = None) -> Manifest:
    slug = project_slug or slugify(project_name)
    return Manifest(
        project_name=project_name,
        project_slug=slug,
        created_at=_now(),
        updated_at=_now(),
        files=[],
    )


def read_manifest(storage: S3Storage, slug: str) -> Manifest | None:
    key = storage.manifest_key(slug)
    if not storage.exists(key):
        return None
    raw = storage.get_bytes(key)
    return Manifest.model_validate_json(raw)


def write_manifest(storage: S3Storage, manifest: Manifest) -> Manifest:
    manifest.updated_at = _now()
    body = manifest.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")
    storage.put_bytes(storage.manifest_key(manifest.project_slug), body, content_type="application/json")
    return manifest


def get_or_create(storage: S3Storage, project_name: str) -> Manifest:
    slug = slugify(project_name)
    existing = read_manifest(storage, slug)
    if existing:
        return existing
    m = new_manifest(project_name, slug)
    return write_manifest(storage, m)


def add_file(
    storage: S3Storage,
    slug: str,
    filename: str,
    file_id: str | None = None,
    status: FileStatus = "uploaded",
) -> tuple[Manifest, ManifestFile]:
    m = read_manifest(storage, slug)
    if m is None:
        raise FileNotFoundError(f"Project not found: {slug}")
    file = ManifestFile(
        file_id=file_id or str(uuid.uuid4()),
        original_filename=filename,
        uploaded_at=_now(),
        status=status,
    )
    m.files.append(file)
    write_manifest(storage, m)
    return m, file


def remove_file(storage: S3Storage, slug: str, file_id: str) -> Manifest:
    m = read_manifest(storage, slug)
    if m is None:
        raise FileNotFoundError(f"Project not found: {slug}")
    before = len(m.files)
    m.files = [f for f in m.files if f.file_id != file_id]
    if len(m.files) == before:
        raise FileNotFoundError(f"File not found: {file_id}")
    write_manifest(storage, m)
    return m


def update_file(
    storage: S3Storage,
    slug: str,
    file_id: str,
    **updates: Any,
) -> ManifestFile:
    m = read_manifest(storage, slug)
    if m is None:
        raise FileNotFoundError(f"Project not found: {slug}")
    f = m.file(file_id)
    if f is None:
        raise FileNotFoundError(f"File not found: {file_id}")
    for k, v in updates.items():
        setattr(f, k, v)
    write_manifest(storage, m)
    return f
