"""Chunked upload: staging round-trip on the real dev DB + API-level end-to-end with a mocked ingest."""
from __future__ import annotations

import base64

from starlette.testclient import TestClient

from app.db import execute, fetch_all
from app.routers.admin import ensure_upload_chunks

UID = "chunked-upload-test"
CSV = b"EID,Date,Sex,Breed,Treatment,Mob,Final Pweight\n982 123536700001,01/01/2023,Steer,Angus,Drench,Front,500.0\n"


def test_chunks_reassemble_in_index_order():
    """Staging is order-independent: rows stored out of order still reassemble correctly."""
    ensure_upload_chunks()
    raw = bytes(range(256)) * 1000  # 256 KB of arbitrary bytes
    chunks = [raw[i : i + 100_000] for i in range(0, len(raw), 100_000)]
    execute("DELETE FROM upload_chunks WHERE upload_id = %s", (UID,))
    try:
        for i, chunk in reversed(list(enumerate(chunks))):
            execute(
                "INSERT INTO upload_chunks (upload_id, chunk_index, data) VALUES (%s, %s, %s)",
                (UID, i, base64.b64encode(chunk).decode()),
            )
        rows = fetch_all("SELECT data FROM upload_chunks WHERE upload_id = %s ORDER BY chunk_index", (UID,))
        assert len(rows) == len(chunks)
        assert b"".join(base64.b64decode(r["data"]) for r in rows) == raw
    finally:
        execute("DELETE FROM upload_chunks WHERE upload_id = %s", (UID,))


def test_chunked_upload_end_to_end(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    """init -> chunk -> finalize drives the same ingest path as the single-request upload."""
    monkeypatch.setattr("app.routers.admin.fetch_one", lambda q, p: {"cnt": 1, "id": 100})
    monkeypatch.setattr("app.routers.admin.execute_returning", lambda q, p: {"id": 1})
    monkeypatch.setattr("app.routers.admin.execute", lambda q, p: None)
    monkeypatch.setattr("app.routers.admin.refresh_forecasts", lambda farm_id: {"status": "updated"})
    monkeypatch.setattr(
        "app.routers.admin.fetch_all",
        lambda q, p: [{"data": base64.b64encode(CSV).decode()}],
    )

    r = client.post("/api/admin/farms/KF/upload/init", json={"filename": "KF_2023-01-01.csv"}, headers=admin_headers)
    assert r.status_code == 200
    uid = r.json()["upload_id"]

    r = client.put(f"/api/admin/farms/KF/upload/{uid}/chunks/0", content=CSV, headers=admin_headers)
    assert r.status_code == 200 and r.json()["bytes_received"] == len(CSV)

    r = client.post(
        f"/api/admin/farms/KF/upload/{uid}/finalize",
        json={"filename": "KF_2023-01-01.csv", "duplicate_mode": "skip", "forecast": False},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["rows_skipped"] == 1 and r.json()["rows_inserted"] == 0


def test_chunked_upload_rejects_bad_filename(client: TestClient, admin_headers: dict[str, str]):
    r = client.post("/api/admin/farms/KF/upload/init", json={"filename": "WRONG_2023-01-01.csv"}, headers=admin_headers)
    assert r.status_code == 400


def test_chunk_reupload_is_idempotent(client: TestClient, admin_headers: dict[str, str]):
    """A retried chunk (response lost after storage) upserts instead of 500ing on the PK."""
    from app.db import fetch_all

    r = client.post("/api/admin/farms/KF/upload/init", json={"filename": "KF_2023-01-01.csv"}, headers=admin_headers)
    uid = r.json()["upload_id"]
    try:
        first = client.put(f"/api/admin/farms/KF/upload/{uid}/chunks/0", content=b"a" * 500, headers=admin_headers)
        retry = client.put(f"/api/admin/farms/KF/upload/{uid}/chunks/0", content=b"b" * 400, headers=admin_headers)
        assert first.status_code == 200 and retry.status_code == 200
        rows = fetch_all("SELECT data FROM upload_chunks WHERE upload_id = %s AND chunk_index = 0", (uid,))
        assert len(rows) == 1
        import base64
        assert base64.b64decode(rows[0]["data"]) == b"b" * 400
    finally:
        execute("DELETE FROM upload_chunks WHERE upload_id = %s", (uid,))
