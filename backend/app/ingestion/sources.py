"""Where OpenAir data comes from (section 5 'Source options'). Only the fetch step differs.

A  manual upload          -> api/imports.py
B  scheduled export file  -> pull_inbox(): files dropped in IMPORT_WATCH_DIR (local folder) or the
                             storage backend's inbox/ prefix (bucket); each new file is loaded once.
C  OpenAir REST API       -> fetch_openair_report(): published-reports endpoint with OAuth 2.0.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.ingestion import pipeline
from app.ingestion.storage import get_storage
from app.models import RawFile

log = logging.getLogger(__name__)
SUPPORTED = (".csv", ".xlsx", ".xls")


def _already_loaded(db, checksum: str) -> bool:
    return db.scalar(select(RawFile.id).where(RawFile.checksum == checksum, RawFile.source != "upload").limit(1)) is not None


def pull_inbox(actor: str = "scheduler") -> list[str]:
    """Load every new file from the watched folder / bucket inbox, oldest name first."""
    s = get_settings()
    found: list[tuple[str, bytes, str]] = []
    if s.IMPORT_WATCH_DIR and os.path.isdir(s.IMPORT_WATCH_DIR):
        for p in sorted(Path(s.IMPORT_WATCH_DIR).iterdir()):
            if p.suffix.lower() in SUPPORTED and p.is_file():
                found.append((p.name, p.read_bytes(), "folder"))
    storage = get_storage()
    for key in storage.list("inbox"):
        if key.lower().endswith(SUPPORTED):
            found.append((key.rsplit("/", 1)[-1], storage.get(key), "bucket"))
    run_ids = []
    for name, content, src in found:
        checksum = hashlib.sha256(content).hexdigest()
        with SessionLocal() as db:
            if _already_loaded(db, checksum):
                continue
            rf = pipeline.store_raw(db, content, name, src, actor)
            db.commit()
            rid = rf.id
        run_ids.append(str(pipeline.run_import(rid, actor=actor, source=src)))
    return run_ids


def openair_configured() -> bool:
    s = get_settings()
    return bool(s.OPENAIR_BASE_URL and s.OPENAIR_CLIENT_ID and s.OPENAIR_REPORT_ID)


def fetch_openair_report() -> bytes:
    """IMP-6: get a published report from the OpenAir REST API using an OAuth 2.0 refresh token."""
    s = get_settings()
    base = s.OPENAIR_BASE_URL.rstrip("/")
    tok = httpx.post(
        f"{base}/login/oauth2/v1/token",
        data={"grant_type": "refresh_token", "refresh_token": s.OPENAIR_REFRESH_TOKEN, "client_id": s.OPENAIR_CLIENT_ID, "client_secret": s.OPENAIR_CLIENT_SECRET},
        timeout=30,
    )
    tok.raise_for_status()
    access = tok.json()["access_token"]
    r = httpx.get(f"{base}/rest/v1/reports/{s.OPENAIR_REPORT_ID}", headers={"Authorization": f"Bearer {access}", "Accept": "text/csv"}, timeout=120)
    r.raise_for_status()
    return r.content


def pull_openair(actor: str = "scheduler") -> str | None:
    if not openair_configured():
        return None
    content = fetch_openair_report()
    with SessionLocal() as db:
        rf = pipeline.store_raw(db, content, f"openair_report_{get_settings().OPENAIR_REPORT_ID}.csv", "api", actor)
        db.commit()
        rid = rf.id
    return str(pipeline.run_import(rid, actor=actor, source="api"))


def scheduled_pull(actor: str = "scheduler") -> list[str]:
    runs = []
    try:
        r = pull_openair(actor)
        if r:
            runs.append(r)
    except Exception:
        log.exception("OpenAir API pull failed")
    runs += pull_inbox(actor)
    return runs
