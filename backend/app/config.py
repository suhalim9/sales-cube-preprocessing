"""Runtime configuration sourced from environment variables.

``Settings`` is loaded once at process start. The S3 bucket name, region,
and CORS origins must be supplied via env vars (or a ``.env`` file). All
other knobs have sensible defaults.

For local dev, copy ``.env.example`` to ``.env`` and fill in the bucket.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # S3
    s3_bucket: str = "keye-cube-cleaner-dev"
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = None  # set for MinIO / localstack
    presigned_url_ttl: int = 900  # 15 min — short, matches DATA_MODEL.md

    # CORS — comma-separated list
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
