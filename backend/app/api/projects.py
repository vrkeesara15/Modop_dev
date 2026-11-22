"""Projects and demand forecast (DEM-1..5), release on 0% (DEM-4), match suggestions (AVL-3)."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import Lookups, get_or_404, opt_uuid, parse_uuid, project_dict, strip_rates
from app.auth.deps import CurrentUser, forbid, get_current_user, require_roles
from app.db import get_db
from app.models import Allocation, DemandLine, Person, Project, TechLevel
from app.services import alerts as alerts_svc
from app.services import audit, matching, metrics
from app.services.rules import week_range, week_start

router = APIRouter(prefix="/api", tags=["projects"])
STATUSES = ("active", "pipeline", "on_hold", "lost", "closed")
CONFIDENCES = (0, 25, 50, 75, 100)


@router.get("/projects")
def list_projects(status: str | None = None, mine: bool = False, q: str | None = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    lk = Lookups(db)
    qry = select(Project).where(Project.is_deleted.is_(False))
    if status:
        qry = qry.where(Project.status.in_(status.split(",")))
    if mine:
        qry = qry.where(Project.pl_user_id == user.id)
    projects = db.scalars(qry.order_by(Project.name)).all()
    if q:
        projects = [p for p in projects if q.lower() in p.name.lower() or q.lower() in (p.client or "").lower()]
    lines = db.scalars(select(DemandLine).where(DemandLine.project_id.in_([p.id for p in projects]), DemandLine.status != "cancelled")).all()
    by_p: dict = {}
    for dl in lines:
        by_p.setdefault(dl.project_id, []).append(dl)
    headcount = dict(
        db.execute(
            select(Allocation.project_id, Allocation.person_id).where(Allocation.is_deleted.is_(False), Allocation.state.in_(("confirmed", "proposed", "requested")))
        ).all()
    )
    people_per_project: dict = {}
    for pid, person in db.execute(select(Allocation.project_id, Allocation.person_id).where(Allocation.is_deleted.is_(False), Allocation.state.in_(("confirmed", "proposed", "requested")))):
        people_per_project.setdefault(pid, set()).add(person)
    out = []
    for p in projects:
        d = project_dict(p, lk, user)
        dls = by_p.get(p.id, [])
        d["demand_lines"] = len(dls)
        d["open_lines"] = sum(1 for x in dls if x.status in ("open", "partly"))
        d["min_confidence"] = min((x.confidence for x in dls), default=None)
        d["people"] = len(people_per_project.get(p.id, ()))
        out.append(d)
    _ = headcount
    return out


class ProjectIn(BaseModel):
    name: str = Field(..., min_length=1)
    client: str | None = None
    status: str = "pipeline"
    billable: bool = True
    start_date: date | None = None
    end_date: date | None = None
    openair_project_id: str | None = None
    pl_user_id: str | None = None

    @field_validator("status")
    @classmethod
    def _st(cls, v):
        if v not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        return v


@router.post("/projects")
def create_project(body: ProjectIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles("pl", "admin", "gl", "sl"))):
    """DEM-5: create a pipeline project before it exists in OpenAir (linked on a later import)."""
    pl = opt_uuid(body.pl_user_id) if (body.pl_user_id and user.is_admin) else user.id
    p = Project(
        name=body.name.strip(), client=body.client, status=body.status, billable=body.billable, start_date=body.start_date, end_date=body.end_date,
        openair_project_id=body.openair_project_id or None, pl_user_id=pl, source="app", updated_by=user.email,
    )
    db.add(p)
    db.flush()
    audit.log(db, entity="project", entity_id=p.id, field=None, old=None, new=p.name, actor=user.email, action="insert")
    db.commit()
    return project_dict(p, Lookups(db), user)


class ProjectPatch(BaseModel):
    name: str | None = None
    client: str | None = None
    status: str | None = None
    billable: bool | None = None
    start_date: date | None = None
    end_date: date | None = None
    openair_project_id: str | None = None
    pl_user_id: str | None = None
    reason: str | None = None


def _can_edit_project(user: CurrentUser, p: Project) -> bool:
    return user.is_admin or user.is_pl_of(p)


@router.get("/projects/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    p = get_or_404(db, Project, project_id)
    lk = Lookups(db)
    d = project_dict(p, lk, user)
    d["demand"] = demand_payload(db, p, lk, user)
    allocs = db.execute(select(Allocation, Person).join(Person, Person.id == Allocation.person_id).where(Allocation.project_id == p.id, Allocation.is_deleted.is_(False))).all()
    d["allocations"] = [
        {
            "id": str(a.id),
            "person_id": str(per.id),
            "person_name": per.name,
            "tech_level": lk.level_code(per.tech_level_id),
            "state": a.state,
            "demand_line_id": str(a.demand_line_id) if a.demand_line_id else None,
            "total_hours": round(sum(float(w.hours) for w in a.weeks), 2),
            "first_week": min((w.week_start for w in a.weeks), default=None),
            "last_week": max((w.week_start for w in a.weeks), default=None),
        }
        for a, per in allocs
    ]
    return strip_rates(d, user)


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    p = get_or_404(db, Project, project_id)
    if not _can_edit_project(user, p):
        raise forbid("Only the project's PL or an Admin can edit it")
    ch = body.model_dump(exclude_unset=True, exclude={"reason"})
    if "status" in ch and ch["status"] not in STATUSES:
        raise HTTPException(422, "bad status")
    if "pl_user_id" in ch:
        if not user.is_admin:
            ch.pop("pl_user_id")
        else:
            ch["pl_user_id"] = opt_uuid(ch["pl_user_id"])
    audit.log_fields(db, entity="project", obj=p, changes=ch, actor=user.email, reason=body.reason)
    affected = set(db.scalars(select(Allocation.person_id).where(Allocation.project_id == p.id)))
    alerts_svc.recompute(db, affected, actor=user.email)
    db.commit()
    return project_dict(p, Lookups(db), user)


@router.post("/projects/{project_id}/lost")
def mark_lost(project_id: str, release: bool = True, reason: str | None = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Mark a project lost: every demand line goes to 0%, people are released and their GLs alerted."""
    p = get_or_404(db, Project, project_id)
    if not _can_edit_project(user, p):
        raise forbid("Only the project's PL or an Admin can mark it lost")
    for dl in db.scalars(select(DemandLine).where(DemandLine.project_id == p.id, DemandLine.status != "cancelled")):
        if dl.confidence != 0:
            audit.log(db, entity="demand_line", entity_id=dl.id, field="confidence", old=dl.confidence, new=0, actor=user.email, reason=reason)
            dl.confidence = 0
    audit.log(db, entity="project", entity_id=p.id, field="status", old=p.status, new="lost", actor=user.email, reason=reason)
    p.status = "lost"
    released = release_people(db, p, None, user, reason) if release else []
    db.commit()
    return {"project": project_dict(p, Lookups(db), user), "released": released}


def release_people(db: Session, p: Project, line: DemandLine | None, user: CurrentUser, reason: str | None) -> list[dict]:
    q = select(Allocation).where(Allocation.project_id == p.id, Allocation.is_deleted.is_(False), Allocation.state.in_(("proposed", "requested", "confirmed")))
    if line is not None:
        q = q.where(Allocation.demand_line_id == line.id)
    allocs = db.scalars(q).all()
    people = []
    for a in allocs:
        audit.log(db, entity="allocation", entity_id=a.id, field="state", old=a.state, new="released", actor=user.email, reason=reason or "project at 0%")
        a.state, a.edited_in_app, a.updated_by = "released", True, user.email
        for w in a.weeks:
            if float(w.hours) > 0:
                w.pending_openair = True
        people.append(db.get(Person, a.person_id))
    uniq = {pp.id: pp for pp in people if pp}
    alerts_svc.project_zero(db, p, list(uniq.values()), user.email)
    alerts_svc.recompute(db, set(uniq), actor=user.email)
    return [{"person_id": str(pp.id), "name": pp.name} for pp in uniq.values()]


# ---------------------------------------------------------------- demand lines
def demand_payload(db: Session, p: Project, lk: Lookups, user: CurrentUser) -> list[dict]:
    lines = db.scalars(select(DemandLine).where(DemandLine.project_id == p.id).order_by(DemandLine.start_week, DemandLine.created_at)).all()
    fills = metrics.demand_fill(db, lines)
    out = []
    for dl in lines:
        out.append(demand_dict(db, dl, lk, fills.get(dl.id, {})))
    return out


def demand_dict(db: Session, dl: DemandLine, lk: Lookups, fill: dict | None = None) -> dict:
    if fill is None:
        fill = metrics.demand_fill(db, [dl]).get(dl.id, {})
    lvl = lk.levels.get(dl.min_tech_level_id)
    n_people = len(set(db.scalars(select(Allocation.person_id).where(Allocation.demand_line_id == dl.id, Allocation.is_deleted.is_(False), Allocation.state.in_(("proposed", "requested", "confirmed"))))))
    req_person = db.get(Person, dl.requested_person_id) if dl.requested_person_id else None
    return {
        "id": str(dl.id),
        "project_id": str(dl.project_id),
        "role_family": dl.role_family,
        "min_tech_level_id": str(dl.min_tech_level_id) if dl.min_tech_level_id else None,
        "min_tech_level": lvl.code if lvl else None,
        "skills": dl.skills or [],
        "hours_per_week": float(dl.hours_per_week),
        "weighted_hours_per_week": round(float(dl.hours_per_week) * dl.confidence / 100, 2),
        "start_week": dl.start_week.isoformat(),
        "end_week": dl.end_week.isoformat(),
        "confidence": dl.confidence,
        "requested_person_id": str(dl.requested_person_id) if dl.requested_person_id else None,
        "requested_person_name": req_person.name if req_person else None,
        "status": dl.status,
        "notes": dl.notes,
        "people_attached": n_people,
        "requested_rate": lk.rate(dl.min_tech_level_id, dl.role_family),
        "fill_by_week": {w.isoformat(): v for w, v in sorted(fill.items())},
        "at_risk": dl.confidence < 75 or dl.status in ("open", "partly"),
    }


class DemandIn(BaseModel):
    role_family: str | None = None
    min_tech_level_id: str | None = None
    skills: list[str] = []
    hours_per_week: float = Field(..., gt=0, le=80)
    start_week: date
    end_week: date
    confidence: int = 100
    requested_person_id: str | None = None
    notes: str | None = None

    @field_validator("confidence")
    @classmethod
    def _c(cls, v):
        if v not in CONFIDENCES:
            raise ValueError("confidence must be one of 0/25/50/75/100")
        return v


@router.get("/projects/{project_id}/demand")
def list_demand(project_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    p = get_or_404(db, Project, project_id)
    return strip_rates(demand_payload(db, p, Lookups(db), user), user)


@router.post("/projects/{project_id}/demand")
def add_demand(project_id: str, body: DemandIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """DEM-2 / DEM-3: PL adds a demand line, optionally naming a person (soft request)."""
    p = get_or_404(db, Project, project_id)
    if not _can_edit_project(user, p):
        raise forbid("Only the project's PL or an Admin can add demand")
    if body.end_week < body.start_week:
        raise HTTPException(422, "end_week before start_week")
    dl = DemandLine(
        project_id=p.id, role_family=body.role_family, min_tech_level_id=opt_uuid(body.min_tech_level_id), skills=[s.strip() for s in body.skills if s.strip()],
        hours_per_week=body.hours_per_week, start_week=week_start(body.start_week), end_week=week_start(body.end_week), confidence=body.confidence,
        requested_person_id=opt_uuid(body.requested_person_id), notes=body.notes, updated_by=user.email,
    )
    db.add(dl)
    db.flush()
    audit.log(db, entity="demand_line", entity_id=dl.id, field=None, old=None, new=f"{p.name}: {body.role_family or 'any'} {body.hours_per_week:g} h/wk @ {body.confidence}%", actor=user.email, action="insert")
    affected = {dl.requested_person_id} if dl.requested_person_id else set()
    alerts_svc.recompute(db, affected, actor=user.email)
    db.commit()
    return strip_rates(demand_dict(db, dl, Lookups(db)), user)


class DemandPatch(BaseModel):
    role_family: str | None = None
    min_tech_level_id: str | None = None
    skills: list[str] | None = None
    hours_per_week: float | None = Field(None, gt=0, le=80)
    start_week: date | None = None
    end_week: date | None = None
    confidence: int | None = None
    requested_person_id: str | None = None
    status: str | None = None
    notes: str | None = None
    reason: str | None = None


@router.patch("/demand/{demand_id}")
def patch_demand(demand_id: str, body: DemandPatch, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    dl = db.get(DemandLine, parse_uuid(demand_id))
    if dl is None:
        raise HTTPException(404, "demand line not found")
    p = db.get(Project, dl.project_id)
    if not _can_edit_project(user, p):
        raise forbid("Only the project's PL or an Admin can edit demand")
    ch = body.model_dump(exclude_unset=True, exclude={"reason"})
    if "confidence" in ch and ch["confidence"] not in CONFIDENCES:
        raise HTTPException(422, "confidence must be one of 0/25/50/75/100")
    for k in ("min_tech_level_id", "requested_person_id"):
        if k in ch:
            ch[k] = opt_uuid(ch[k])
    for k in ("start_week", "end_week"):
        if ch.get(k):
            ch[k] = week_start(ch[k])
    old_person = dl.requested_person_id
    went_zero = ch.get("confidence") == 0 and dl.confidence != 0
    audit.log_fields(db, entity="demand_line", obj=dl, changes=ch, actor=user.email, reason=body.reason)
    attached = db.scalars(select(Allocation).where(Allocation.demand_line_id == dl.id, Allocation.is_deleted.is_(False), Allocation.state.in_(("proposed", "requested", "confirmed")))).all()
    affected = {a.person_id for a in attached} | {x for x in (old_person, dl.requested_person_id) if x}
    alerts_svc.recompute(db, affected, actor=user.email)
    db.commit()
    out = demand_dict(db, dl, Lookups(db))
    if went_zero:
        people = {a.person_id: db.get(Person, a.person_id) for a in attached}
        # DEM-4: prompt "Release N people?"
        out["release_prompt"] = {"count": len(people), "people": [{"person_id": str(k), "name": v.name} for k, v in people.items()]}
    return strip_rates(out, user)


@router.post("/demand/{demand_id}/release")
def release_demand(demand_id: str, reason: str | None = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    dl = db.get(DemandLine, parse_uuid(demand_id))
    if dl is None:
        raise HTTPException(404)
    p = db.get(Project, dl.project_id)
    if not _can_edit_project(user, p):
        raise forbid()
    released = release_people(db, p, dl, user, reason)
    db.commit()
    return {"released": released}


@router.delete("/demand/{demand_id}")
def delete_demand(demand_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    dl = db.get(DemandLine, parse_uuid(demand_id))
    if dl is None:
        raise HTTPException(404)
    p = db.get(Project, dl.project_id)
    if not _can_edit_project(user, p):
        raise forbid()
    audit.log(db, entity="demand_line", entity_id=dl.id, field="status", old=dl.status, new="cancelled", actor=user.email, action="delete")
    dl.status = "cancelled"
    affected = set(db.scalars(select(Allocation.person_id).where(Allocation.demand_line_id == dl.id)))
    if dl.requested_person_id:
        affected.add(dl.requested_person_id)
    alerts_svc.recompute(db, affected, actor=user.email)
    db.commit()
    return {"ok": True}


@router.get("/demand/{demand_id}/matches")
def demand_matches(demand_id: str, limit: int = 5, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """AVL-3: top matches ranked by level fit, skill match, free hours and project continuity."""
    dl = db.get(DemandLine, parse_uuid(demand_id))
    if dl is None:
        raise HTTPException(404)
    return strip_rates(matching.suggest(db, dl, limit=limit), user)


@router.get("/demand/open")
def open_demand(frm: date | None = None, to: date | None = None, mine: bool = False, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    from app.api.common import window

    start, end = window(frm, to)
    ids = user.pl_project_ids if mine else None
    return {"weeks": [w.isoformat() for w in week_range(start, end)], "rows": metrics.open_demand(db, start, end, ids)}


_ = TechLevel
