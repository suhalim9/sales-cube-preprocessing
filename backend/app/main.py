"""FastAPI app entry point.

Run with: ``uv run uvicorn app.main:app --reload``
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.routes import router
from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title="Sales Cube Cleaner",
    version="0.1.0",
    description="Backend for the cube-cleaning demo. See DATA_MODEL.md for the contract.",
)

# Compress responses larger than 1 KB. The detection list on stress.parquet
# is ~50 MB of raw JSON (hundreds of thousands of detections); gzipping
# cuts that to ~5 MB on the wire, which dominates transfer time on the
# review pane's first load. Apply audit logs and large preview pages also
# benefit. Browsers send Accept-Encoding: gzip by default so this is a
# transparent win.
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "bucket": settings.s3_bucket, "region": settings.s3_region}
