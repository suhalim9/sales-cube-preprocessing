"""End-to-end route tests: happy-path workflow + key error paths.

Each test goes through the API the same way the frontend would —
create project → upload file → parse → detect → apply → download.
"""

from __future__ import annotations

import io
import json

import pandas as pd
import requests
from fastapi.testclient import TestClient


def _upload(client: TestClient, slug: str, filename: str, body: bytes) -> str:
    """POST file create → PUT to presigned URL → return file_id."""
    resp = client.post(
        f"/api/projects/{slug}/files",
        json={"filename": filename, "content_type": "application/vnd.apache.parquet"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    url = data["upload_url"]
    headers = data["upload_headers"]
    # Hit the presigned URL exactly like a browser would.
    put = requests.put(url, data=body, headers=headers)
    assert put.status_code == 200, put.text
    return data["file_id"]


# ---------------------------------------------------------------------------
# Project + healthcheck
# ---------------------------------------------------------------------------


def test_healthcheck(client: TestClient):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_get_project_creates_on_first_call(client: TestClient):
    r = client.get("/api/projects/my-project")
    assert r.status_code == 200
    body = r.json()
    assert body["project_slug"] == "my-project"
    assert body["files"] == []


def test_get_project_persists(client: TestClient):
    client.get("/api/projects/my-project")
    # Second call returns same project, not a new one
    r = client.get("/api/projects/my-project")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Upload (UP-01..02)
# ---------------------------------------------------------------------------


def test_upload_parquet_returns_presigned_url(client: TestClient, happy_parquet_bytes):
    client.get("/api/projects/acme")
    r = client.post(
        "/api/projects/acme/files",
        json={"filename": "sales.parquet"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "file_id" in body
    assert "upload_url" in body
    assert "Content-Type" in body["upload_headers"]


def test_upload_non_parquet_rejected(client: TestClient):
    client.get("/api/projects/acme")
    r = client.post(
        "/api/projects/acme/files",
        json={"filename": "sales.csv"},
    )
    assert r.status_code == 400
    assert "Parquet" in r.json()["detail"]


def test_upload_to_missing_project_404(client: TestClient):
    r = client.post("/api/projects/no-such-project/files", json={"filename": "x.parquet"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Parse + Schema (SCH-01)
# ---------------------------------------------------------------------------


def test_parse_then_get_schema(client: TestClient, happy_parquet_bytes):
    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "sales.parquet", happy_parquet_bytes)

    r = client.post(f"/api/projects/acme/files/{file_id}/parse", json={"filename": "sales.parquet"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id_columns"] == ["customer", "product_line"]
    assert len(body["time_columns"]) == 4
    assert body["time_format"] == "YYYY_M"
    assert body["hard_errors"] == []

    # Manifest reflects the parsed state
    r = client.get("/api/projects/acme")
    files = r.json()["files"]
    assert files[0]["status"] == "schema_pending"
    assert files[0]["row_count"] == 4


def test_parse_before_upload_returns_400(client: TestClient):
    client.get("/api/projects/acme")
    # Create the file record but don't PUT the body to the presigned URL.
    r = client.post("/api/projects/acme/files", json={"filename": "x.parquet"})
    file_id = r.json()["file_id"]
    r = client.post(f"/api/projects/acme/files/{file_id}/parse", json={"filename": "sales.parquet"})
    assert r.status_code == 400


def test_parse_corrupted_parquet_returns_400(client: TestClient):
    """UP-05: file passes magic-byte sniff but body is junk."""
    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "corrupt.parquet", b"PAR1" + b"\x00" * 100)
    r = client.post(
        f"/api/projects/acme/files/{file_id}/parse",
        json={"filename": "corrupt.parquet"},
    )
    assert r.status_code == 400
    assert "corrupted" in r.json()["detail"].lower()


def test_parse_zero_row_parquet_returns_400(client: TestClient):
    """UP-07: valid Parquet schema but zero rows."""
    df = pd.DataFrame({"customer": [], "product_line": [], "2022_1": []}).astype({"2022_1": "float64"})
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    buf.seek(0)
    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "empty.parquet", buf.read())
    r = client.post(
        f"/api/projects/acme/files/{file_id}/parse",
        json={"filename": "empty.parquet"},
    )
    assert r.status_code == 400
    assert "no data rows" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_detect_returns_results_and_updates_status(client: TestClient, happy_parquet_bytes):
    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "sales.parquet", happy_parquet_bytes)
    client.post(f"/api/projects/acme/files/{file_id}/parse", json={"filename": "sales.parquet"})

    r = client.post(f"/api/projects/acme/files/{file_id}/detect")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] > 0
    assert set(body["counts"].keys()) == {"negative", "refund", "double_booking", "outlier"}
    # Each detection has the required shape
    for d in body["detections"]:
        assert {"detection_id", "row_idx", "row_key", "column", "value",
                "flagged_by", "suggested_fix", "confidence", "alternative_fixes"} <= d.keys()

    r = client.get("/api/projects/acme")
    assert r.json()["files"][0]["status"] == "detected"


def test_detections_endpoint_after_detect(client: TestClient, happy_parquet_bytes):
    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "sales.parquet", happy_parquet_bytes)
    client.post(f"/api/projects/acme/files/{file_id}/parse", json={"filename": "sales.parquet"})
    client.post(f"/api/projects/acme/files/{file_id}/detect")

    r = client.get(f"/api/projects/acme/files/{file_id}/detections")
    assert r.status_code == 200
    assert r.json()["total"] > 0


def test_detections_and_preview_heal_from_sidecar_after_restart(
    client: TestClient, happy_parquet_bytes
):
    """Simulates a Fly machine restart between /detect and the next call.
    /detections and /preview must read the persisted S3 sidecar instead of
    recomputing — otherwise the new counter-based detection_ids wouldn't
    match what the frontend has staged, and every /apply selection would 400.
    """
    from app.api import routes

    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "sales.parquet", happy_parquet_bytes)
    client.post(f"/api/projects/acme/files/{file_id}/parse", json={"filename": "sales.parquet"})

    detect = client.post(f"/api/projects/acme/files/{file_id}/detect").json()
    original_ids = [d["detection_id"] for d in detect["detections"]]
    assert len(original_ids) > 0

    # Wipe every in-process cache — equivalent to a machine restart.
    routes._DETECTION_CACHE.clear()
    routes._ANOMALY_ROW_CACHE.clear()
    routes._DATAFRAME_CACHE.clear()

    # /detections must come back with the EXACT same IDs from the sidecar.
    r = client.get(f"/api/projects/acme/files/{file_id}/detections")
    assert r.status_code == 200, r.text
    healed_ids = [d["detection_id"] for d in r.json()["detections"]]
    assert healed_ids == original_ids, "detection_ids must persist across restart"

    # Clear again and check /preview with the detected-row filter — it should
    # heal from the sidecar rather than 409-ing.
    routes._DETECTION_CACHE.clear()
    routes._ANOMALY_ROW_CACHE.clear()

    r = client.get(f"/api/projects/acme/files/{file_id}/preview?detected=any")
    assert r.status_code == 200, r.text
    assert r.json()["total"] > 0


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_preview_returns_rows_and_columns(client: TestClient, happy_parquet_bytes):
    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "sales.parquet", happy_parquet_bytes)
    client.post(f"/api/projects/acme/files/{file_id}/parse", json={"filename": "sales.parquet"})

    r = client.get(f"/api/projects/acme/files/{file_id}/preview")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 4
    assert len(body["rows"]) == 4
    assert body["columns"][:2] == ["customer", "product_line"]


# ---------------------------------------------------------------------------
# Apply (APP-02, APP-04)
# ---------------------------------------------------------------------------


def test_apply_writes_cleaned_and_audit(client: TestClient, happy_parquet_bytes):
    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "sales.parquet", happy_parquet_bytes)
    client.post(f"/api/projects/acme/files/{file_id}/parse", json={"filename": "sales.parquet"})
    detect = client.post(f"/api/projects/acme/files/{file_id}/detect")
    detections = detect.json()["detections"]

    selections = [
        {"detection_id": d["detection_id"], "fix": d["suggested_fix"]}
        for d in detections
    ]
    r = client.post(
        f"/api/projects/acme/files/{file_id}/apply",
        json={"selections": selections},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_changes"] >= len(detections)  # split adds extras
    assert body["cleaned_url"].endswith("/cleaned-url")
    assert body["audit_url"].endswith("/audit-url")

    # File status flipped
    r = client.get("/api/projects/acme")
    assert r.json()["files"][0]["status"] == "cleaned"


def test_apply_twice_returns_409(client: TestClient, happy_parquet_bytes):
    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "sales.parquet", happy_parquet_bytes)
    client.post(f"/api/projects/acme/files/{file_id}/parse", json={"filename": "sales.parquet"})
    client.post(f"/api/projects/acme/files/{file_id}/detect")
    client.post(f"/api/projects/acme/files/{file_id}/apply", json={"selections": []})

    r = client.post(f"/api/projects/acme/files/{file_id}/apply", json={"selections": []})
    assert r.status_code == 409
    assert "already cleaned" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Presigned downloads
# ---------------------------------------------------------------------------


def test_cleaned_and_audit_urls_after_apply(client: TestClient, happy_parquet_bytes):
    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "sales.parquet", happy_parquet_bytes)
    client.post(f"/api/projects/acme/files/{file_id}/parse", json={"filename": "sales.parquet"})
    client.post(f"/api/projects/acme/files/{file_id}/detect")
    client.post(f"/api/projects/acme/files/{file_id}/apply", json={"selections": []})

    r = client.get(f"/api/projects/acme/files/{file_id}/cleaned-url")
    assert r.status_code == 200
    cleaned_url = r.json()["url"]

    r = client.get(f"/api/projects/acme/files/{file_id}/audit-url")
    assert r.status_code == 200
    audit_url = r.json()["url"]

    # Both URLs should be fetchable
    r = requests.get(cleaned_url)
    assert r.status_code == 200
    assert r.content.startswith(b"PAR1")

    r = requests.get(audit_url)
    assert r.status_code == 200
    audit = json.loads(r.content)
    assert "file_id" in audit
    assert "changes" in audit


def test_cleaned_url_404_before_apply(client: TestClient, happy_parquet_bytes):
    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "sales.parquet", happy_parquet_bytes)
    client.post(f"/api/projects/acme/files/{file_id}/parse", json={"filename": "sales.parquet"})

    r = client.get(f"/api/projects/acme/files/{file_id}/cleaned-url")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Schema override (SCH-09)
# ---------------------------------------------------------------------------


def test_schema_override_returns_validation(client: TestClient, happy_parquet_bytes):
    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "sales.parquet", happy_parquet_bytes)
    client.post(f"/api/projects/acme/files/{file_id}/parse", json={"filename": "sales.parquet"})

    # Override: try to mark `customer` as a measure (non-numeric → reject)
    r = client.patch(
        f"/api/projects/acme/files/{file_id}/schema",
        json={
            "id_columns": ["product_line"],
            "time_columns": ["2022_1", "2022_2", "2022_3", "2022_4"],
            "measure_columns": ["customer", "2022_1", "2022_2", "2022_3", "2022_4"],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert any("non-numeric" in e for e in body["hard_errors"])


def test_schema_get_surfaces_time_sort_warning_after_override(client: TestClient):
    """After the user saves a schema override on a file whose on-disk time
    columns weren't chronological, GET /schema must surface the auto-sort
    soft warning. The validator against the pre-sorted override won't notice
    the disorder; the GET route's secondary check against on-disk column
    order is what surfaces it."""
    df = pd.DataFrame({
        "customer": ["A", "B", "C"],
        "2022_5": [100.0, 200.0, 300.0],
        "2022_1": [50.0, 80.0, 60.0],
        "2022_3": [70.0, 90.0, 110.0],
    })
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, compression="snappy")
    buf.seek(0)

    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "shuffled.parquet", buf.read())
    client.post(
        f"/api/projects/acme/files/{file_id}/parse",
        json={"filename": "shuffled.parquet"},
    )

    # Persist the sorted override.
    r = client.patch(
        f"/api/projects/acme/files/{file_id}/schema",
        json={
            "id_columns": ["customer"],
            "time_columns": ["2022_1", "2022_3", "2022_5"],
            "measure_columns": ["2022_1", "2022_3", "2022_5"],
        },
    )
    assert r.status_code == 200, r.text

    # GET /schema re-validates against the override AND does the on-disk
    # chronology check — that's where the auto-sort warning lives.
    r = client.get(f"/api/projects/acme/files/{file_id}/schema")
    assert r.status_code == 200, r.text
    body = r.json()
    assert any("chronological" in w for w in body["soft_warnings"])


# ---------------------------------------------------------------------------
# Coerce (SCH-14)
# ---------------------------------------------------------------------------


def _coercible_parquet_bytes() -> bytes:
    """One time-named column has a stray text cell — eligible for coercion.
    Needs >=11 rows so a single bad cell stays under the 10% loss threshold.
    """
    n = 12
    df = pd.DataFrame({
        "customer": [f"C{i}" for i in range(n)],
        "2022_1": [100.0 + i for i in range(n)],
        "2022_2": ["n/a", *[str(200.0 + i) for i in range(1, n)]],
        "2022_3": [300.0 + i for i in range(n)],
    })
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, compression="snappy")
    buf.seek(0)
    return buf.read()


def test_coerce_promotes_column_to_measure(client: TestClient):
    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "sales.parquet", _coercible_parquet_bytes())
    parsed = client.post(
        f"/api/projects/acme/files/{file_id}/parse", json={"filename": "sales.parquet"}
    ).json()
    assert "2022_2" in parsed["coercible_columns"]
    assert "2022_2" not in parsed["measure_columns"]

    r = client.post(
        f"/api/projects/acme/files/{file_id}/coerce",
        json={"columns": ["2022_2"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "2022_2" in body["measure_columns"]
    assert body["coercible_columns"] == []


def test_coerce_unknown_column_returns_400(client: TestClient, happy_parquet_bytes):
    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "sales.parquet", happy_parquet_bytes)
    client.post(f"/api/projects/acme/files/{file_id}/parse", json={"filename": "sales.parquet"})

    r = client.post(
        f"/api/projects/acme/files/{file_id}/coerce",
        json={"columns": ["nope"]},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Delete file
# ---------------------------------------------------------------------------


def test_delete_file_removes_from_manifest(client: TestClient, happy_parquet_bytes):
    client.get("/api/projects/acme")
    file_id = _upload(client, "acme", "sales.parquet", happy_parquet_bytes)
    client.post(f"/api/projects/acme/files/{file_id}/parse", json={"filename": "sales.parquet"})

    r = client.delete(f"/api/projects/acme/files/{file_id}")
    assert r.status_code == 204

    manifest = client.get("/api/projects/acme").json()
    assert all(f["file_id"] != file_id for f in manifest["files"])

    # Idempotent: a second DELETE still returns 204 (no-op cleanup).
    r2 = client.delete(f"/api/projects/acme/files/{file_id}")
    assert r2.status_code == 204
