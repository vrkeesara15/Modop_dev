"""People directory, person page, skills, availability flags, capacity exceptions (PPL-1..5, AVL-1/2)."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.common import Lookups, get_or_404, opt_uuid, parse_uuid, person_dict, project_dict, strip_rates, window
from app.auth.deps import CurrentUser, forbid, get_current_user
from app.db import get_db
from app.models import (
    Allocation,
    AllocationWeek,
    AvailabilityFlag,
    CapacityException,
    ChangeLog,
    Group,
    Person,
    PersonSkill,
    Project,
    Skill,
    TechLevel,
)
from app.services import alerts as alerts_svc
from app.services import audit, metrics
from app.services import settings as settings_svc
from app.services.rules import effective_confidence, is_billable, week_start

router = APIRouter(prefix="/api", tags=["people"])


@router.get("/tech-levels")
def tech_levels(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return [{"id": str(t.id), "code": t.code, "rank": t.rank, "description": t.description} for t in db.scalars(select(TechLevel).order_by(TechLevel.rank))]


@router.get("/groups")
def groups(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    lk = Lookups(db)
    counts = dict(db.execute(select(Person.group_id, func.count()).where(Person.is_deleted.is_(False), Person.active.is_(True)).group_by(Person.group_id)).all())
    return [
        {
            "id": str(g.id),
            "name": g.name,
            "gl_user_id": str(g.gl_user_id) if g.gl_user_id else None,
            "gl_name": lk.user_name(g.gl_user_id),
            "sl_user_id": str(g.sl_user_id) if g.sl_user_id else None,
            "sl_name": lk.user_name(g.sl_user_id),
            "parent_group_id": str(g.parent_group_id) if g.parent_group_id else None,
            "headcount": counts.get(g.id, 0),
        }
        for g in sorted(lk.groups.values(), key=lambda g: g.name)
    ]


@router.get("/skills")
def skills(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return [{"id": str(s.id), "name": s.name, "category": s.category} for s in db.scalars(select(Skill).order_by(Skill.name))]


@router.get("/people")
def list_people(
    group: str | None = None,
    level: str | None = None,
    skill: str | None = None,
    role_family: str | None = None,
    location: str | None = None,
    q: str | None = None,
    available_from: date | None = None,
    available_to: date | None = None,
    min_free: float = 0,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Directory (PPL-1); with available_from/available_to it becomes the availability search (AVL-1/2)."""
    if available_from or available_to or min_free:
        start, end = window(available_from, available_to, 4)
        rows = metrics.availability(db, start, end, tech_level=level, role_family=role_family, skill=skill, group_id=opt_uuid(group), location=location, min_free=min_free)
        if q:
            rows = [r for r in rows if q.lower() in r["name"].lower()]
        return strip_rates({"weeks": [w.isoformat() for w in metrics.week_range(start, end)], "people": rows}, user)
    lk = Lookups(db)
    people = metrics.people_query(db, group_id=opt_uuid(group))
    if level:
        codes = {c.strip().lower() for c in level.split(",")}
        people = [p for p in people if (lk.level_code(p.tech_level_id) or "").lower() in codes]
    if role_family:
        people = [p for p in people if (p.role_family or "").lower() == role_family.lower()]
    if location:
        people = [p for p in people if (p.location or "").lower() == location.lower()]
    if skill:
        wanted = {s.strip().lower() for s in skill.split(",")}
        people = [p for p in people if wanted <= {ps.skill.name.lower() for ps in p.skills}]
    if q:
        people = [p for p in people if q.lower() in p.name.lower() or q.lower() in (p.email or "").lower()]
    start = metrics.current_week()
    pws = metrics.person_weeks(db, start, start + timedelta(weeks=3), people=people)
    out = []
    for p in people:
        d = person_dict(p, lk, user)
        weeks = list(pws.get(p.id, {}).values())
        if weeks:
            d["util_forecast_4w"] = round(sum((w.util_forecast or 0) for w in weeks) / len(weeks), 1)
            d["util_confirmed_4w"] = round(sum((w.util_confirmed or 0) for w in weeks) / len(weeks), 1)
            d["status_now"] = weeks[0].status_forecast
        out.append(d)
    return out


@router.get("/people/{person_id}")
def get_person(person_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    p = get_or_404(db, Person, person_id)
    lk = Lookups(db)
    d = person_dict(p, lk, user)
    d["availability_flags"] = [
        {"id": str(f.id), "from_date": f.from_date.isoformat(), "hours_per_week": float(f.hours_per_week), "note": f.note, "set_by": f.set_by}
        for f in db.scalars(select(AvailabilityFlag).where(AvailabilityFlag.person_id == p.id, AvailabilityFlag.active.is_(True)).order_by(AvailabilityFlag.from_date))
    ]
    d["capacity_exceptions"] = [
        {"id": str(c.id), "date": c.date.isoformat(), "hours_off": float(c.hours_off), "reason": c.reason}
        for c in db.scalars(select(CapacityException).where(CapacityException.person_id == p.id, CapacityException.date >= date.today() - timedelta(days=60)).order_by(CapacityException.date))
    ]
    return d


class PersonPatch(BaseModel):
    role_family: str | None = None
    location: str | None = None
    std_hours_week: float | None = Field(None, ge=0, le=80)
    group_id: str | None = None
    tech_level_id: str | None = None
    reason: str | None = None


@router.patch("/people/{person_id}")
def patch_person(person_id: str, body: PersonPatch, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    p = get_or_404(db, Person, person_id)
    if not (user.is_admin or user.is_gl_of(p)):
        raise forbid("Only the person's GL or an Admin can edit people")
    ch = body.model_dump(exclude_unset=True, exclude={"reason"})
    for k in ("group_id", "tech_level_id"):
        if ch.get(k):
            ch[k] = parse_uuid(ch[k], k)
    audit.log_fields(db, entity="person", obj=p, changes=ch, actor=user.email, reason=body.reason)
    alerts_svc.recompute(db, {p.id}, actor=user.email)
    db.commit()
    return person_dict(p, Lookups(db), user)


@router.get("/people/{person_id}/timeline")
def timeline(person_id: str, frm: date | None = None, to: date | None = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """PPL-2: weekly timeline, 4 weeks back and 12 forward by default, with projects and billable split."""
    p = get_or_404(db, Person, person_id)
    cur = metrics.current_week()
    start, end = window(frm or cur - timedelta(weeks=4), to or cur + timedelta(weeks=11))
    cfg = settings_svc.get_all(db)
    pw = metrics.person_weeks(db, start, end, people=[p], cfg=cfg).get(p.id, {})
    lk = Lookups(db)
    rows = db.execute(
        select(Allocation, Project)
        .join(Project, Project.id == Allocation.project_id)
        .where(Allocation.person_id == p.id, Allocation.is_deleted.is_(False))
    ).all()
    from app.models import DemandLine

    dl_conf = {d.id: d.confidence for d in db.scalars(select(DemandLine).where(DemandLine.id.in_([a.demand_line_id for a, _ in rows if a.demand_line_id])))}
    projects = []
    for a, prj in rows:
        weeks = {w.week_start.isoformat(): float(w.hours) for w in a.weeks if start <= w.week_start <= end}
        if not weeks and a.state not in ("requested", "proposed"):
            continue
        visible = user.can_see_detail(p, prj, cfg["visibility_mode"])
        conf = 0 if prj.status in metrics.INACTIVE_PROJECT else effective_confidence(a.state, dl_conf.get(a.demand_line_id), a.booking_type, float(cfg["soft_booking_weight"]))
        projects.append(
            {
                "allocation_id": str(a.id),
                "project": project_dict(prj, lk) if visible else {"id": None, "name": "Other commitments", "client": None},
                "state": a.state,
                "source": a.source,
                "billable": bool(a.billable) and prj.status == "active" and prj.billable,
                "confidence": conf,
                "task_name": a.task_name if visible else None,
                "weeks": weeks,
                "total_hours": round(sum(weeks.values()), 2),
            }
        )
    weeks = [v.to_dict() for _, v in sorted(pw.items())]
    tot_bill = sum(w["billable_hours"] for w in weeks)
    tot_conf = sum(w["confirmed_hours"] for w in weeks)
    return {
        "person": person_dict(p, lk, user),
        "weeks": weeks,
        "projects": sorted(projects, key=lambda x: -x["total_hours"]),
        "billable_split": {"billable_hours": round(tot_bill, 2), "non_billable_hours": round(tot_conf - tot_bill, 2)},
        "current_week": cur.isoformat(),
    }


class SkillIn(BaseModel):
    name: str
    proficiency: int = Field(1, ge=1, le=3)


@router.put("/people/{person_id}/skills")
def put_skills(person_id: str, body: list[SkillIn], db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """PPL-4: GL edits skills and proficiency."""
    p = get_or_404(db, Person, person_id)
    if not (user.is_admin or user.is_gl_of(p) or (p.email and p.email.lower() == user.email.lower())):
        raise forbid("Only the person's GL or an Admin can edit skills")
    existing = {ps.skill.name.lower(): ps for ps in p.skills}
    wanted = {s.name.strip().lower(): s for s in body if s.name.strip()}
    for key, ps in list(existing.items()):
        if key not in wanted:
            audit.log(db, entity="person_skill", entity_id=p.id, field=ps.skill.name, old=ps.proficiency, new=None, actor=user.email, action="delete")
            p.skills.remove(ps)
    for key, s in wanted.items():
        if key in existing:
            ps = existing[key]
            if ps.proficiency != s.proficiency:
                audit.log(db, entity="person_skill", entity_id=p.id, field=ps.skill.name, old=ps.proficiency, new=s.proficiency, actor=user.email)
                ps.proficiency = s.proficiency
                ps.updated_by = user.email
            continue
        sk = db.scalar(select(Skill).where(func.lower(Skill.name) == key))
        if sk is None:
            sk = Skill(name=s.name.strip(), updated_by=user.email)
            db.add(sk)
            db.flush()
        p.skills.append(PersonSkill(person_id=p.id, skill_id=sk.id, proficiency=s.proficiency, updated_by=user.email, skill=sk))
        audit.log(db, entity="person_skill", entity_id=p.id, field=sk.name, old=None, new=s.proficiency, actor=user.email, action="insert")
    db.commit()
    db.refresh(p)
    return person_dict(p, Lookups(db), user)["skills"]


class FlagIn(BaseModel):
    from_date: date
    hours_per_week: float = Field(..., ge=0, le=80)
    note: str | None = None


@router.post("/people/{person_id}/availability")
def add_flag(person_id: str, body: FlagIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """PPL-3: GL marks a person 'Available from <date> for <hours>/week'."""
    p = get_or_404(db, Person, person_id)
    if not (user.is_admin or user.is_gl_of(p)):
        raise forbid("Only the person's GL or an Admin can mark availability")
    f = AvailabilityFlag(person_id=p.id, from_date=body.from_date, hours_per_week=body.hours_per_week, note=body.note, set_by=user.email, updated_by=user.email)
    db.add(f)
    db.flush()
    audit.log(db, entity="availability_flag", entity_id=p.id, field="available", old=None, new=f"from {body.from_date} for {body.hours_per_week:g} h/week", actor=user.email, reason=body.note, action="insert")
    db.commit()
    return {"id": str(f.id)}


@router.delete("/people/{person_id}/availability/{flag_id}")
def remove_flag(person_id: str, flag_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    p = get_or_404(db, Person, person_id)
    if not (user.is_admin or user.is_gl_of(p)):
        raise forbid()
    f = db.get(AvailabilityFlag, parse_uuid(flag_id))
    if not f or f.person_id != p.id:
        raise HTTPException(404, "flag not found")
    f.active = False
    f.updated_by = user.email
    audit.log(db, entity="availability_flag", entity_id=p.id, field="available", old=f"from {f.from_date}", new=None, actor=user.email, action="delete")
    db.commit()
    return {"ok": True}


class ExceptionIn(BaseModel):
    date: date
    hours_off: float = Field(..., gt=0, le=24)
    reason: str | None = "PTO"
    days: int = Field(1, ge=1, le=60)


@router.post("/people/{person_id}/capacity-exceptions")
def add_exception(person_id: str, body: ExceptionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """PPL-5: PTO and holidays reduce capacity (manual entry; OpenAir PTO uses the same table)."""
    p = get_or_404(db, Person, person_id)
    if not (user.is_admin or user.is_gl_of(p)):
        raise forbid()
    d, added = body.date, 0
    while added < body.days:
        if d.weekday() < 5:
            db.add(CapacityException(person_id=p.id, date=d, hours_off=body.hours_off, reason=body.reason, updated_by=user.email))
            added += 1
        d += timedelta(days=1)
    audit.log(db, entity="capacity_exception", entity_id=p.id, field="hours_off", old=None, new=f"{body.days} day(s) from {body.date}, {body.hours_off:g} h/day ({body.reason})", actor=user.email, action="insert")
    alerts_svc.recompute(db, {p.id}, actor=user.email)
    db.commit()
    return {"ok": True}


@router.delete("/people/{person_id}/capacity-exceptions/{exc_id}")
def del_exception(person_id: str, exc_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    p = get_or_404(db, Person, person_id)
    if not (user.is_admin or user.is_gl_of(p)):
        raise forbid()
    c = db.get(CapacityException, parse_uuid(exc_id))
    if not c or c.person_id != p.id:
        raise HTTPException(404)
    audit.log(db, entity="capacity_exception", entity_id=p.id, field="hours_off", old=f"{c.date} {float(c.hours_off):g} h", new=None, actor=user.email, action="delete")
    db.delete(c)
    alerts_svc.recompute(db, {p.id}, actor=user.email)
    db.commit()
    return {"ok": True}


@router.get("/people/{person_id}/history")
def person_history(person_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    p = get_or_404(db, Person, person_id)
    alloc_ids = [str(a) for a in db.scalars(select(Allocation.id).where(Allocation.person_id == p.id))]
    rows = db.scalars(
        select(ChangeLog).where(or_(ChangeLog.entity_id == str(p.id), ChangeLog.entity_id.in_(alloc_ids))).order_by(ChangeLog.created_at.desc()).limit(300)
    ).all()
    return [change_dict(c) for c in rows]


def change_dict(c: ChangeLog) -> dict:
    return {
        "id": str(c.id),
        "at": c.created_at.isoformat(),
        "entity": c.entity,
        "entity_id": c.entity_id,
        "entity_key": c.entity_key,
        "field": c.field,
        "old_value": c.old_value,
        "new_value": c.new_value,
        "actor": c.actor,
        "reason": c.reason,
        "action": c.action,
        "run_id": str(c.run_id) if c.run_id else None,
    }


_ = (AllocationWeek, Group, is_billable, week_start)
