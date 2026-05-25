"""S3Storage primitives — bytes round-trip, parquet round-trip, presigned URLs."""

from __future__ import annotations

import io

import pandas as pd
import pytest
import requests

from app.storage.s3 import S3Storage


def test_put_get_bytes_round_trip(storage: S3Storage):
    storage.put_bytes("foo/bar.txt", b"hello world", content_type="text/plain")
    assert storage.get_bytes("foo/bar.txt") == b"hello world"


def test_exists_true_after_put(storage: S3Storage):
    assert storage.exists("missing") is False
    storage.put_bytes("present", b"x")
    assert storage.exists("present") is True


def test_delete_removes_object(storage: S3Storage):
    storage.put_bytes("temp", b"x")
    storage.delete("temp")
    assert storage.exists("temp") is False


def test_parquet_round_trip(storage: S3Storage):
    df = pd.DataFrame({
        "customer": ["A", "B", "C"],
        "2022_1": [100.0, 200.0, -50.0],
        "2022_2": [0.0, 150.0, 75.0],
    })
    storage.put_parquet("cube.parquet", df)
    out = storage.get_parquet("cube.parquet")
    pd.testing.assert_frame_equal(df, out)


def test_presigned_put_then_get(storage: S3Storage):
    put_url = storage.presigned_put("upload-target", content_type="text/plain")
    # PUT via the presigned URL (the same path the browser would take).
    r = requests.put(put_url, data=b"uploaded body", headers={"Content-Type": "text/plain"})
    assert r.status_code == 200

    get_url = storage.presigned_get("upload-target")
    r = requests.get(get_url)
    assert r.status_code == 200
    assert r.content == b"uploaded body"


def test_key_helpers_use_expected_layout(storage: S3Storage):
    assert storage.manifest_key("acme") == "projects/acme/manifest.json"
    assert storage.original_key("acme", "f1") == "projects/acme/files/f1/original.parquet"
    assert storage.cleaned_key("acme", "f1") == "projects/acme/files/f1/cleaned.parquet"
    assert storage.audit_key("acme", "f1") == "projects/acme/files/f1/audit.json"
