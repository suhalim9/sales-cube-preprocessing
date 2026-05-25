"""FastAPI app entry point.

Run with: ``uv run uvicorn app.main:app --reload``
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title="Sales Cube Cleaner",
    version="0.1.0",
    description="Backend for the cube-cleaning demo. See DATA_MODEL.md for the contract.",
)

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
