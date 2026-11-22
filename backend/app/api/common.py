"""Shared helpers for routers: lookups, serialisers, date parsing."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.models import Group, Person, Project, TechLevel, User
from app.services import metrics
from app.services.rules import week_start


def parse_uuid(v, name="id") -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        raise HTTPException(422, f"invalid {name}")


def opt_uuid(v) -> uuid.UUID | None:
    return parse_uuid(v) if v else None


def window(frm: date | None, to: date | None, default_weeks: int = 12) -> tuple[date, date]:
    start = week_start(frm) if frm else metrics.current_week()
    end = week_start(to) if to else start + timedelta(weeks=default_weeks - 1)
    if end < start:
        raise HTTPException(422, "'to' is before 'from'")
    if (end - start).days > 7 * 80:
        raise HTTPException(422, "window too large (max 80 weeks)")
    return start, end


def get_or_404(db: Session, model, id_):
    obj = db.get(model, parse_uuid(id_))
    if obj is None or getattr(obj, "is_deleted", False):
        raise HTTPException(404, f"{model.__name__} not found")
    return obj


class Lookups:
    def __init__(self, db: Session):
        self.levels = {t.id: t for t in db.scalars(select(TechLevel))}
        self.groups = {g.id: g for g in db.scalars(select(Group))}
        self.users = {u.id: u for u in db.scalars(select(User))}
        self.rate = metrics.rate_lookup(db)

    def level_code(self, lid):
        t = self.levels.get(lid)
        return t.code if t else None

    def group_name(self, gid):
        g = self.groups.get(gid)
        return g.name if g else None

    def user_name(self, uid):
        u = self.users.get(uid)
        return u.name if u else None


def person_dict(p: Person, lk: Lookups, user: CurrentUser | None = None, full: bool = True) -> dict:
    g = lk.groups.get(p.group_id)
    d = {
        "id": str(p.id),
        "name": p.name,
        "email": p.email,
        "openair_user_id": p.openair_user_id,
        "group_id": str(p.group_id) if p.group_id else None,
        "group_name": g.name if g else None,
        "gl_user_id": str(g.gl_user_id) if g and g.gl_user_id else None,
        "gl_name": lk.user_name(g.gl_user_id) if g else None,
        "tech_level_id": str(p.tech_level_id) if p.tech_level_id else None,
        "tech_level": lk.level_code(p.tech_level_id),
        "tech_level_rank": lk.levels[p.tech_level_id].rank if p.tech_level_id in lk.levels else None,
        "role_family": p.role_family,
        "location": p.location,
        "std_hours_week": float(p.std_hours_week or 0),
        "manager_name": p.manager_name,
        "source": "openair" if p.source_key else "app",
        "skills": sorted(({"name": s.skill.name, "proficiency": s.proficiency, "category": s.skill.category} for s in p.skills), key=lambda x: x["name"]),
    }
    if user is None or user.can_see_rates:
        d["bill_rate"] = lk.rate(p.tech_level_id, p.role_family)
    if user is not None:
        d["can_edit"] = user.is_admin or user.is_gl_of(p)
    return d


def project_dict(p: Project, lk: Lookups, user: CurrentUser | None = None) -> dict:
    d = {
        "id": str(p.id),
        "name": p.name,
        "client": p.client,
        "openair_project_id": p.openair_project_id,
        "pl_user_id": str(p.pl_user_id) if p.pl_user_id else None,
        "pl_name": lk.user_name(p.pl_user_id) or p.pl_name,
        "status": p.status,
        "billable": p.billable,
        "start_date": p.start_date.isoformat() if p.start_date else None,
        "end_date": p.end_date.isoformat() if p.end_date else None,
        "source": p.source,
    }
    if user is not None:
        d["can_edit"] = user.is_admin or user.is_pl_of(p)
    return d


def strip_rates(obj, user: CurrentUser):
    """Remove bill-rate fields server-side for roles without access."""
    if user.can_see_rates:
        return obj
    if isinstance(obj, list):
        return [strip_rates(o, user) for o in obj]
    if isinstance(obj, dict):
        return {k: strip_rates(v, user) for k, v in obj.items() if "rate" not in k and "margin" not in k}
    return obj
