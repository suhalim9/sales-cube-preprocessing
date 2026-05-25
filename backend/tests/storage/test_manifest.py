"""Manifest CRUD via S3."""

from __future__ import annotations

import pytest

from app.storage.manifest import (
    Manifest,
    add_file,
    get_or_create,
    new_manifest,
    read_manifest,
    slugify,
    update_file,
    write_manifest,
)


def test_slugify_kebab_lowercase():
    assert slugify("Acme Diligence Q1") == "acme-diligence-q1"


def test_slugify_collapses_non_alnum():
    assert slugify("Acme — Q1 — España") == "acme-q1-espa-a"


def test_slugify_empty_falls_back():
    assert slugify("!!!") == "untitled"


def test_slugify_caps_at_64_chars():
    long = "x" * 200
    assert len(slugify(long)) == 64


def test_get_or_create_new_persists(storage):
    m = get_or_create(storage, "Acme")
    assert m.project_slug == "acme"
    assert m.files == []
    # Persisted to S3
    read = read_manifest(storage, "acme")
    assert read is not None
    assert read.project_name == "Acme"


def test_get_or_create_returns_existing(storage):
    m1 = get_or_create(storage, "Acme")
    m2 = get_or_create(storage, "Acme")
    assert m1.created_at == m2.created_at


def test_read_missing_returns_none(storage):
    assert read_manifest(storage, "does-not-exist") is None


def test_add_file_appends_and_persists(storage):
    get_or_create(storage, "Acme")
    m, f = add_file(storage, "acme", "sales.parquet")
    assert len(m.files) == 1
    assert f.original_filename == "sales.parquet"
    assert f.status == "uploaded"
    # Roundtrip
    re_read = read_manifest(storage, "acme")
    assert re_read is not None
    assert len(re_read.files) == 1


def test_add_file_to_missing_project_raises(storage):
    with pytest.raises(FileNotFoundError):
        add_file(storage, "missing", "x.parquet")


def test_update_file_changes_status(storage):
    get_or_create(storage, "Acme")
    _, f = add_file(storage, "acme", "sales.parquet")
    update_file(storage, "acme", f.file_id, status="detected", row_count=487_000)
    m = read_manifest(storage, "acme")
    assert m is not None
    file = m.file(f.file_id)
    assert file is not None
    assert file.status == "detected"
    assert file.row_count == 487_000


def test_update_file_unknown_raises(storage):
    get_or_create(storage, "Acme")
    with pytest.raises(FileNotFoundError):
        update_file(storage, "acme", "nope", status="cleaned")


def test_write_manifest_refreshes_updated_at(storage):
    m = new_manifest("Acme")
    original = m.updated_at
    # Force a different second
    import time
    time.sleep(1.01)
    write_manifest(storage, m)
    assert m.updated_at > original
