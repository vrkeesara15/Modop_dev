"""Admin-editable settings stored in the `settings` table, with defaults."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppSetting
from app.services.rules import DEFAULT_BANDS, DEFAULT_CONFIDENCE_STEPS

DEFAULTS: dict = {
    "bands": DEFAULT_BANDS,
    "confidence_steps": DEFAULT_CONFIDENCE_STEPS,
    "std_hours_week": 40,
    "visibility_mode": "open",  # open | scoped
    "soft_booking_weight": 0.5,
    "spike_threshold": 0.6,
    "unfilled_alert_days": 14,
    "rolloff_alert_days": 14,
    "import_cron": None,  # falls back to IMPORT_CRON env var
    "role_families": ["Data Engineer", "Developer", "Analyst", "Designer", "PM"],
    "locations": [],
    "digest_enabled": False,
}


def get_all(db: Session) -> dict:
    rows = {r.key: r.value for r in db.scalars(select(AppSetting))}
    out = {}
    for k, v in DEFAULTS.items():
        out[k] = rows.get(k, v)
    for k, v in rows.items():
        out.setdefault(k, v)
    if isinstance(out.get("bands"), dict):
        out["bands"] = {**DEFAULT_BANDS, **out["bands"]}
    return out


def get(db: Session, key: str):
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    return row.value if row is not None else DEFAULTS.get(key)


def put(db: Session, key: str, value, actor: str) -> tuple[object, object]:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    old = row.value if row else DEFAULTS.get(key)
    if row is None:
        row = AppSetting(key=key, value=value, updated_by=actor)
        db.add(row)
    else:
        row.value = value
        row.updated_by = actor
    return old, value
