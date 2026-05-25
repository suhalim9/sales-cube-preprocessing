"""Shared moto fixtures for storage tests."""

from __future__ import annotations

import os

import boto3
import pytest
from moto import mock_aws

from app.config import Settings
from app.storage.s3 import S3Storage, ensure_bucket


@pytest.fixture(autouse=True)
def _aws_creds(monkeypatch):
    """Stop boto3 from picking up real ~/.aws creds during tests."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def storage():
    with mock_aws():
        settings = Settings(s3_bucket="test-bucket", s3_region="us-east-1")
        s = S3Storage(settings=settings)
        ensure_bucket(s)
        yield s
