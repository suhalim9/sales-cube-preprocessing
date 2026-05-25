"""Shared API test fixtures — TestClient + moto-mocked S3."""

from __future__ import annotations

import io

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from app.config import Settings


@pytest.fixture(autouse=True)
def _aws_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_REGION", "us-east-1")


@pytest.fixture
def client(monkeypatch):
    """FastAPI TestClient with moto-backed S3 and the bucket pre-created."""
    with mock_aws():
        # Reset Settings cache so env vars take effect.
        from app.config import get_settings
        get_settings.cache_clear()

        from app.main import app
        from app.storage.s3 import S3Storage, ensure_bucket
        ensure_bucket(S3Storage())
        with TestClient(app) as c:
            yield c


@pytest.fixture
def happy_parquet_bytes() -> bytes:
    """Build a small valid Parquet payload for upload tests."""
    df = pd.DataFrame({
        "customer": ["Cust_A", "Cust_B", "Cust_C", "Cust_D"],
        "product_line": ["X", "Y", "X", "Y"],
        "2022_1": [100.0, -50.0, 0.0, 200.0],
        "2022_2": [120.0, 110.0, 0.0, 0.0],  # (200, 0) for double-booking row 3
        "2022_3": [-1000.0, 130.0, 0.0, 90.0],
        "2022_4": [80.0, 100.0, 0.0, 95.0],
    })
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, compression="snappy")
    buf.seek(0)
    return buf.read()
