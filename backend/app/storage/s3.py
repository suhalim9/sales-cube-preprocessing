"""Thin wrapper around boto3's S3 client.

Five primitives cover everything the app needs:

- ``put_bytes`` / ``get_bytes`` — small JSON sidecars (manifest, audit log).
- ``put_parquet`` / ``get_parquet`` — DataFrame in/out via PyArrow.
- ``exists`` — manifest probe before reading.
- ``presigned_put`` / ``presigned_get`` — short-lived URLs for browser uploads
  and cleaned-file downloads.

Bucket name, region, and an optional endpoint override (for moto/MinIO
during tests) come from ``app.config.Settings``.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import boto3
import pandas as pd
import pyarrow.parquet as pq

from app.config import Settings, get_settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class S3Storage:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        kwargs = {"region_name": self.settings.s3_region}
        if self.settings.s3_endpoint_url:
            kwargs["endpoint_url"] = self.settings.s3_endpoint_url
        self.client: S3Client = boto3.client("s3", **kwargs)
        self.bucket = self.settings.s3_bucket

    # ----- key helpers ----------------------------------------------------

    def manifest_key(self, project_slug: str) -> str:
        return f"projects/{project_slug}/manifest.json"

    def original_key(self, project_slug: str, file_id: str) -> str:
        return f"projects/{project_slug}/files/{file_id}/original.parquet"

    def cleaned_key(self, project_slug: str, file_id: str) -> str:
        return f"projects/{project_slug}/files/{file_id}/cleaned.parquet"

    def audit_key(self, project_slug: str, file_id: str) -> str:
        return f"projects/{project_slug}/files/{file_id}/audit.json"

    def detections_key(self, project_slug: str, file_id: str) -> str:
        return f"projects/{project_slug}/files/{file_id}/detections.json"

    # ----- primitives -----------------------------------------------------

    def put_bytes(self, key: str, body: bytes, content_type: str = "application/octet-stream") -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)

    def get_bytes(self, key: str) -> bytes:
        resp = self.client.get_object(Bucket=self.bucket, Key=key)
        return resp["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    # ----- parquet round-trip --------------------------------------------

    def put_parquet(self, key: str, df: pd.DataFrame) -> None:
        buf = io.BytesIO()
        df.to_parquet(buf, index=False, compression="snappy")
        buf.seek(0)
        self.put_bytes(key, buf.read(), content_type="application/vnd.apache.parquet")

    def get_parquet(self, key: str) -> pd.DataFrame:
        body = self.get_bytes(key)
        # Check for duplicate column names at the schema level — pyarrow's
        # read_table() throws ArrowInvalid on dups, which our /parse handler
        # would otherwise translate to "corrupted". Better to raise a specific
        # error the route can catch and report as SCH-15.
        buf = io.BytesIO(body)
        schema = pq.read_schema(buf)
        names = schema.names
        dups = sorted({n for i, n in enumerate(names) if names.index(n) != i})
        if dups:
            raise DuplicateColumnsError(dups)
        buf.seek(0)
        return pq.read_table(buf).to_pandas()

    # ----- presigned URLs -------------------------------------------------

    def presigned_put(self, key: str, content_type: str = "application/octet-stream") -> str:
        return self.client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=self.settings.presigned_url_ttl,
        )

    def presigned_get(self, key: str, download_filename: str | None = None) -> str:
        params: dict[str, str] = {"Bucket": self.bucket, "Key": key}
        if download_filename:
            # Browsers honor Content-Disposition for the save-as filename;
            # without this S3 hands back the bare object key (e.g. "cleaned.parquet").
            params["ResponseContentDisposition"] = (
                f'attachment; filename="{download_filename}"'
            )
        return self.client.generate_presigned_url(
            ClientMethod="get_object",
            Params=params,
            ExpiresIn=self.settings.presigned_url_ttl,
        )


class DuplicateColumnsError(Exception):
    """Raised when a Parquet file has duplicate column names. Caught by the
    /parse route and surfaced as a hard error matching SCH-15."""

    def __init__(self, duplicates: list[str]) -> None:
        self.duplicates = duplicates
        super().__init__(f"Duplicate column names: {duplicates}")


def ensure_bucket(storage: S3Storage) -> None:
    """Idempotent create-bucket for local dev (moto/minio). Production
    pre-provisions the bucket; this is a no-op when it already exists."""
    try:
        storage.client.head_bucket(Bucket=storage.bucket)
    except storage.client.exceptions.ClientError:
        kwargs = {"Bucket": storage.bucket}
        if storage.settings.s3_region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {
                "LocationConstraint": storage.settings.s3_region
            }
        storage.client.create_bucket(**kwargs)
