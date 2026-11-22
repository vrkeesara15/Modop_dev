"""ALR-1: in-app alert feed per user."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import parse_uuid
from app.auth.deps import CurrentUser, get_current_user
from app.db import get_db
from app.models import Alert, Person, Project

router = APIRouter(prefix="/api/alerts", tags=["alerts"])
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def alert_dict(a: Alert, people: dict, projects: dict) -> dict:
    return {
        "id": str(a.id),
        "type": a.type,
        "severity": a.severity,
        "status": a.status,
        "message": a.message,
        "person_id": str(a.person_id) if a.person_id else None,
        "person_name": people.get(a.person_id),
        "project_id": str(a.project_id) if a.project_id else None,
        "project_name": projects.get(a.project_id),
        "week_start": a.week_start.isoformat() if a.week_start else None,
        "created_at": a.created_at.isoformat(),
    }


@router.get("")
def list_alerts(status: str | None = "new,seen", limit: int = 200, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    q = select(Alert).where(Alert.user_id == user.id)
    if status:
        q = q.where(Alert.status.in_(status.split(",")))
    rows = db.scalars(q.order_by(Alert.created_at.desc()).limit(limit)).all()
    people = dict(db.execute(select(Person.id, Person.name)).all())
    projects = dict(db.execute(select(Project.id, Project.name)).all())
    unread = db.scalar(select(func.count()).select_from(Alert).where(Alert.user_id == user.id, Alert.status == "new"))
    items = [alert_dict(a, people, projects) for a in rows]
    items.sort(key=lambda a: (a["status"] != "new", SEVERITY_ORDER.get(a["severity"], 3), a["created_at"]), reverse=False)
    return {"unread": unread, "items": items}


class AlertPatch(BaseModel):
    ids: list[str] | None = None
    status: str
    all: bool = False


@router.patch("")
def patch_alerts(body: AlertPatch, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if body.status not in ("new", "seen", "done"):
        raise HTTPException(422, "status must be new, seen or done")
    q = select(Alert).where(Alert.user_id == user.id)
    if not body.all:
        q = q.where(Alert.id.in_([parse_uuid(i) for i in body.ids or []]))
    n = 0
    for a in db.scalars(q):
        a.status, a.updated_by = body.status, user.email
        n += 1
    db.commit()
    return {"updated": n}


@router.patch("/{alert_id}")
def patch_alert(alert_id: str, body: AlertPatch, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    a = db.get(Alert, parse_uuid(alert_id))
    if a is None or a.user_id != user.id:
        raise HTTPException(404)
    if body.status not in ("new", "seen", "done"):
        raise HTTPException(422)
    a.status, a.updated_by = body.status, user.email
    db.commit()
    return {"ok": True}
