"""FastAPI dependencies — storage handle + slug/file lookups."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.config import Settings, get_settings
from app.storage.manifest import Manifest, ManifestFile, read_manifest
from app.storage.s3 import S3Storage


def _storage(settings: Annotated[Settings, Depends(get_settings)]) -> S3Storage:
    return S3Storage(settings=settings)


Storage = Annotated[S3Storage, Depends(_storage)]


def _require_project(
    slug: str,
    storage: Storage,
) -> Manifest:
    m = read_manifest(storage, slug)
    if m is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{slug}' not found")
    return m


RequiredProject = Annotated[Manifest, Depends(_require_project)]


def _require_file(
    file_id: str,
    manifest: RequiredProject,
) -> ManifestFile:
    f = manifest.file(file_id)
    if f is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File '{file_id}' not found")
    return f


RequiredFile = Annotated[ManifestFile, Depends(_require_file)]
