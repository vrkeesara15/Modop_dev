"""Administration (ADM-1, ADM-2): users, roles, groups, thresholds, confidence steps, rate cards."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import Lookups, opt_uuid, parse_uuid
from app.auth.deps import CurrentUser, get_current_user, require_roles
from app.auth.local import hash_password
from app.db import get_db
from app.models import Group, Person, RateCard, TechLevel, User, UserRole
from app.services import alerts as alerts_svc
from app.services import audit
from app.services import settings as settings_svc

router = APIRouter(prefix="/api/admin", tags=["admin"])
admin_only = require_roles("admin")
ROLES = ("admin", "sl", "gl", "pl", "member")
EDITABLE = set(settings_svc.DEFAULTS)


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return settings_svc.get_all(db)


@router.put("/settings")
def put_settings(body: dict, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles("admin", "sl"))):
    """Admins edit everything; SLs may edit thresholds (bands) only."""
    unknown = set(body) - EDITABLE
    if unknown:
        raise HTTPException(422, f"unknown settings: {sorted(unknown)}")
    if not user.is_admin and set(body) - {"bands"}:
        raise HTTPException(403, "Senior Leaders can only change thresholds")
    if "bands" in body:
        b = body["bands"]
        try:
            vals = [float(b[k]) for k in ("bench", "light", "healthy", "hot")]
        except (KeyError, TypeError, ValueError):
            raise HTTPException(422, "bands need numeric bench, light, healthy, hot")
        if vals != sorted(vals) or vals[0] <= 0:
            raise HTTPException(422, "bands must be increasing and positive")
        body["bands"] = dict(zip(("bench", "light", "healthy", "hot"), vals))
    if "visibility_mode" in body and body["visibility_mode"] not in ("open", "scoped"):
        raise HTTPException(422, "visibility_mode must be open or scoped")
    if "confidence_steps" in body:
        steps = body["confidence_steps"]
        if not isinstance(steps, list) or {int(s["value"]) for s in steps} - {0, 25, 50, 75, 100}:
            raise HTTPException(422, "confidence steps use the fixed values 0/25/50/75/100; only labels are editable")
    if "std_hours_week" in body:
        try:
            new_std = float(body["std_hours_week"])
        except (TypeError, ValueError):
            raise HTTPException(422, "std_hours_week must be a number")
        if not 0 < new_std <= 80:
            raise HTTPException(422, "std_hours_week must be between 0 and 80")
        old_std = float(settings_svc.get(db, "std_hours_week"))
        if abs(new_std - old_std) > 0.005:
            # people still on the organisation default follow it; per-person overrides are kept
            for per in db.scalars(select(Person).where(Person.std_hours_week == old_std)):
                audit.log(db, entity="person", entity_id=per.id, field="std_hours_week", old=old_std, new=new_std, actor=user.email, reason="organisation standard hours changed")
                per.std_hours_week, per.updated_by = new_std, user.email
    for k, v in body.items():
        old, new = settings_svc.put(db, k, v, user.email)
        audit.log(db, entity="setting", entity_id=k, field=k, old=old, new=new, actor=user.email)
    db.flush()
    if {"bands", "soft_booking_weight", "std_hours_week"} & set(body):
        alerts_svc.recompute(db, None, actor=user.email)
    db.commit()
    return settings_svc.get_all(db)


# ---------------------------------------------------------------- rate cards
class RateIn(BaseModel):
    id: str | None = None
    tech_level_id: str
    role_family: str | None = None
    rate_usd_hr: float = Field(..., ge=0)
    effective_from: date
    effective_to: date | None = None


def rate_dict(r: RateCard, lk: Lookups) -> dict:
    return {"id": str(r.id), "tech_level_id": str(r.tech_level_id), "tech_level": lk.level_code(r.tech_level_id), "role_family": r.role_family, "rate_usd_hr": float(r.rate_usd_hr), "effective_from": r.effective_from.isoformat(), "effective_to": r.effective_to.isoformat() if r.effective_to else None}


@router.get("/rate-cards")
def rate_cards(db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles("admin", "sl", "gl", "pl"))):
    lk = Lookups(db)
    return [rate_dict(r, lk) for r in db.scalars(select(RateCard).order_by(RateCard.effective_from.desc()))]


@router.put("/rate-cards")
def put_rate_cards(body: list[RateIn], db: Session = Depends(get_db), user: CurrentUser = Depends(admin_only)):
    """Replace the rate card with the given rows (rows keep their id when provided)."""
    existing = {r.id: r for r in db.scalars(select(RateCard))}
    keep = set()
    for row in body:
        data = {"tech_level_id": parse_uuid(row.tech_level_id), "role_family": row.role_family or None, "rate_usd_hr": row.rate_usd_hr, "effective_from": row.effective_from, "effective_to": row.effective_to}
        if row.effective_to and row.effective_to < row.effective_from:
            raise HTTPException(422, "effective_to before effective_from")
        rid = opt_uuid(row.id)
        if rid and rid in existing:
            audit.log_fields(db, entity="rate_card", obj=existing[rid], changes=data, actor=user.email)
            keep.add(rid)
        else:
            r = RateCard(**data, updated_by=user.email)
            db.add(r)
            db.flush()
            keep.add(r.id)
            audit.log(db, entity="rate_card", entity_id=r.id, field=None, old=None, new=f"{row.rate_usd_hr} USD/h", actor=user.email, action="insert")
    for rid, r in existing.items():
        if rid not in keep:
            audit.log(db, entity="rate_card", entity_id=rid, field=None, old=f"{float(r.rate_usd_hr)} USD/h", new=None, actor=user.email, action="delete")
            db.delete(r)
    db.commit()
    lk = Lookups(db)
    return [rate_dict(r, lk) for r in db.scalars(select(RateCard).order_by(RateCard.effective_from.desc()))]


class LevelIn(BaseModel):
    code: str
    rank: int
    description: str | None = None


@router.post("/tech-levels")
def add_level(body: LevelIn, db: Session = Depends(get_db), user: CurrentUser = Depends(admin_only)):
    if db.scalar(select(TechLevel).where(func.upper(TechLevel.code) == body.code.upper())):
        raise HTTPException(409, "level exists")
    t = TechLevel(code=body.code.upper(), rank=body.rank, description=body.description, updated_by=user.email)
    db.add(t)
    db.flush()
    audit.log(db, entity="tech_level", entity_id=t.id, field=None, old=None, new=t.code, actor=user.email, action="insert")
    db.commit()
    return {"id": str(t.id), "code": t.code, "rank": t.rank}


# ---------------------------------------------------------------- users & roles
def user_dict(u: User, lk: Lookups) -> dict:
    return {
        "id": str(u.id),
        "email": u.email,
        "name": u.name,
        "is_active": u.is_active,
        "roles": [{"role": r.role, "scope_group_id": str(r.scope_group_id) if r.scope_group_id else None, "scope_group": lk.group_name(r.scope_group_id)} for r in u.roles],
        "gl_of": [g.name for g in lk.groups.values() if g.gl_user_id == u.id],
        "sl_of": [g.name for g in lk.groups.values() if g.sl_user_id == u.id],
        "sso": bool(u.sso_subject),
    }


@router.get("/users")
def users(db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles("admin", "sl"))):
    lk = Lookups(db)
    return [user_dict(u, lk) for u in db.scalars(select(User).order_by(User.name))]


class RoleIn(BaseModel):
    role: str
    scope_group_id: str | None = None


class UserIn(BaseModel):
    email: EmailStr | str
    name: str
    password: str | None = None
    is_active: bool = True
    roles: list[RoleIn] = []


def _apply_roles(db: Session, u: User, roles: list[RoleIn], actor: str):
    for r in roles:
        if r.role not in ROLES:
            raise HTTPException(422, f"role must be one of {ROLES}")
    old = sorted(f"{r.role}:{r.scope_group_id or ''}" for r in u.roles)
    new = sorted(f"{r.role}:{r.scope_group_id or ''}" for r in roles)
    if old != new:
        u.roles.clear()
        db.flush()
        for r in roles:
            u.roles.append(UserRole(role=r.role, scope_group_id=opt_uuid(r.scope_group_id), updated_by=actor))
        audit.log(db, entity="user_roles", entity_id=u.id, field="roles", old=old, new=new, actor=actor)


@router.post("/users")
def create_user(body: UserIn, db: Session = Depends(get_db), user: CurrentUser = Depends(admin_only)):
    email = str(body.email).strip().lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(409, "a user with that email exists")
    u = User(email=email, name=body.name, is_active=body.is_active, password_hash=hash_password(body.password) if body.password else None, updated_by=user.email)
    db.add(u)
    db.flush()
    audit.log(db, entity="user", entity_id=u.id, field=None, old=None, new=email, actor=user.email, action="insert")
    _apply_roles(db, u, body.roles, user.email)
    db.commit()
    return user_dict(u, Lookups(db))


class UserPatch(BaseModel):
    name: str | None = None
    password: str | None = None
    is_active: bool | None = None
    roles: list[RoleIn] | None = None


@router.put("/users/{user_id}")
def update_user(user_id: str, body: UserPatch, db: Session = Depends(get_db), user: CurrentUser = Depends(admin_only)):
    u = db.get(User, parse_uuid(user_id))
    if u is None:
        raise HTTPException(404)
    ch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k in ("name", "is_active")}
    audit.log_fields(db, entity="user", obj=u, changes=ch, actor=user.email)
    if body.password:
        u.password_hash = hash_password(body.password)
        audit.log(db, entity="user", entity_id=u.id, field="password", old="***", new="(changed)", actor=user.email)
    if body.roles is not None:
        _apply_roles(db, u, body.roles, user.email)
    db.commit()
    db.refresh(u)
    return user_dict(u, Lookups(db))


# ---------------------------------------------------------------- groups
class GroupIn(BaseModel):
    name: str
    gl_user_id: str | None = None
    sl_user_id: str | None = None
    parent_group_id: str | None = None


@router.post("/groups")
def create_group(body: GroupIn, db: Session = Depends(get_db), user: CurrentUser = Depends(admin_only)):
    if db.scalar(select(Group).where(func.lower(Group.name) == body.name.lower())):
        raise HTTPException(409, "group exists")
    g = Group(name=body.name, gl_user_id=opt_uuid(body.gl_user_id), sl_user_id=opt_uuid(body.sl_user_id), parent_group_id=opt_uuid(body.parent_group_id), updated_by=user.email)
    db.add(g)
    db.flush()
    audit.log(db, entity="group", entity_id=g.id, field=None, old=None, new=g.name, actor=user.email, action="insert")
    db.commit()
    return {"id": str(g.id)}


@router.put("/groups/{group_id}")
def update_group(group_id: str, body: GroupIn, db: Session = Depends(get_db), user: CurrentUser = Depends(admin_only)):
    g = db.get(Group, parse_uuid(group_id))
    if g is None:
        raise HTTPException(404)
    audit.log_fields(db, entity="group", obj=g, changes={"name": body.name, "gl_user_id": opt_uuid(body.gl_user_id), "sl_user_id": opt_uuid(body.sl_user_id), "parent_group_id": opt_uuid(body.parent_group_id)}, actor=user.email)
    db.commit()
    return {"ok": True}
