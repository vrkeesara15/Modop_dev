"""change_log helpers: every write records who, when, old value, new value and reason (ALC-4, ADM-3)."""
import json
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import ChangeLog


def _s(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v == v.to_integral() else str(float(v))
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str, sort_keys=True)
    return str(v)


def log(
    db: Session,
    *,
    entity: str,
    entity_id,
    field: str | None,
    old,
    new,
    actor: str,
    reason: str | None = None,
    run_id=None,
    action: str = "update",
    entity_key: str | None = None,
) -> ChangeLog | None:
    o, n = _s(old), _s(new)
    if action == "update" and o == n:
        return None
    row = ChangeLog(
        entity=entity,
        entity_id=_s(entity_id),
        entity_key=entity_key,
        field=field,
        old_value=o,
        new_value=n,
        actor=actor,
        reason=reason,
        run_id=run_id,
        action=action,
        updated_by=actor,
    )
    db.add(row)
    return row


def log_fields(db: Session, *, entity: str, obj, changes: dict, actor: str, reason: str | None = None) -> int:
    """Apply `changes` to obj, logging each field that actually changes. Returns number changed."""
    n = 0
    for k, v in changes.items():
        old = getattr(obj, k)
        if _s(old) != _s(v):
            log(db, entity=entity, entity_id=obj.id, field=k, old=old, new=v, actor=actor, reason=reason)
            setattr(obj, k, v)
            n += 1
    if n:
        obj.updated_by = actor
    return n
