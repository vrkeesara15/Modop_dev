"""Requests inbox (AVL-4, ALC-5): a PL requests a person, or a GL offers one.

The allocation becomes Confirmed only when the person's GL and the project's PL have both accepted.
Either can decline with a reason. Admin can override.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.common import Lookups, get_or_404, opt_uuid, parse_uuid
from app.auth.deps import CurrentUser, forbid, get_current_user
from app.db import get_db
from app.models import Allocation, AllocationWeek, DemandLine, Group, Person, Project, StaffingRequest
from app.services import alerts as alerts_svc
from app.services import audit, metrics
from app.services.rules import week_range, week_start

router = APIRouter(prefix="/api/requests", tags=["requests"])


def _gl_of(db: Session, person: Person):
    g = db.get(Group, person.group_id) if person.group_id else None
    return g.gl_user_id if g else None


def request_dict(db: Session, r: StaffingRequest, lk: Lookups, user: CurrentUser) -> dict:
    per, prj = db.get(Person, r.person_id), db.get(Project, r.project_id)
    gl = _gl_of(db, per)
    return {
        "id": str(r.id),
        "kind": r.kind,
        "person_id": str(per.id),
        "person_name": per.name,
        "tech_level": lk.level_code(per.tech_level_id),
        "group_name": lk.group_name(per.group_id),
        "project_id": str(prj.id),
        "project_name": prj.name,
        "client": prj.client,
        "demand_line_id": str(r.demand_line_id) if r.demand_line_id else None,
        "allocation_id": str(r.allocation_id) if r.allocation_id else None,
        "hours_per_week": float(r.hours_per_week),
        "start_week": r.start_week.isoformat(),
        "end_week": r.end_week.isoformat(),
        "created_by": lk.user_name(r.created_by_user_id),
        "created_at": r.created_at.isoformat(),
        "gl_name": lk.user_name(gl),
        "pl_name": lk.user_name(prj.pl_user_id),
        "gl_status": r.gl_status,
        "pl_status": r.pl_status,
        "status": r.status,
        "reason": r.reason,
        "note": r.note,
        "can_act_as_gl": r.status == "pending" and r.gl_status == "pending" and (user.is_admin or user.is_gl_of(per)),
        "can_act_as_pl": r.status == "pending" and r.pl_status == "pending" and (user.is_admin or user.is_pl_of(prj)),
        "can_cancel": r.status == "pending" and (user.is_admin or r.created_by_user_id == user.id),
    }


@router.get("")
def list_requests(box: str = "inbox", status: str | None = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    q = select(StaffingRequest).order_by(StaffingRequest.created_at.desc())
    if status:
        q = q.where(StaffingRequest.status.in_(status.split(",")))
    rows = db.scalars(q).all()
    lk = Lookups(db)
    out = []
    for r in rows:
        d = request_dict(db, r, lk, user)
        mine_to_act = d["can_act_as_gl"] or d["can_act_as_pl"]
        involved = mine_to_act or r.created_by_user_id == user.id or user.is_gl_of(db.get(Person, r.person_id)) or user.is_pl_of(db.get(Project, r.project_id))
        if box == "inbox" and not mine_to_act:
            continue
        if box == "sent" and r.created_by_user_id != user.id:
            continue
        if box == "all" and not (involved or user.has("admin", "sl")):
            continue
        out.append(d)
    return out


class RequestIn(BaseModel):
    kind: str = "request"  # request (PL asks for a person) / offer (GL offers a person)
    person_id: str
    project_id: str
    demand_line_id: str | None = None
    hours_per_week: float = Field(..., gt=0, le=80)
    start_week: date
    end_week: date
    note: str | None = None


@router.post("")
def create_request(body: RequestIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    per = get_or_404(db, Person, body.person_id)
    prj = get_or_404(db, Project, body.project_id)
    if body.kind not in ("request", "offer"):
        raise HTTPException(422, "kind must be request or offer")
    if body.end_week < body.start_week:
        raise HTTPException(422, "end_week before start_week")
    if not user.has("pl", "gl", "admin"):
        raise forbid("Only PLs, GLs and Admins can request or offer people")
    dl = db.get(DemandLine, parse_uuid(body.demand_line_id)) if body.demand_line_id else None
    if dl and dl.project_id != prj.id:
        raise HTTPException(422, "demand line belongs to another project")
    start, end = week_start(body.start_week), week_start(body.end_week)
    a = Allocation(person_id=per.id, project_id=prj.id, demand_line_id=dl.id if dl else None, state="requested", source="app", billable=prj.billable, start_date=start, end_date=end, edited_in_app=True, updated_by=user.email)
    db.add(a)
    db.flush()
    for w in week_range(start, end):
        db.add(AllocationWeek(allocation_id=a.id, week_start=w, hours=body.hours_per_week, edited_in_app=True, pending_openair=True, updated_by=user.email))
    r = StaffingRequest(
        kind=body.kind, person_id=per.id, project_id=prj.id, demand_line_id=a.demand_line_id, allocation_id=a.id, hours_per_week=body.hours_per_week,
        start_week=start, end_week=end, created_by_user_id=user.id, note=body.note, updated_by=user.email,
    )
    # whoever creates it has accepted their own side
    if user.is_gl_of(per):
        r.gl_status = "accepted"
    if user.is_pl_of(prj):
        r.pl_status = "accepted"
    db.add(r)
    db.flush()
    audit.log(db, entity="request", entity_id=r.id, field=None, old=None, new=f"{body.kind}: {per.name} -> {prj.name} {body.hours_per_week:g} h/week {start}..{end}", actor=user.email, reason=body.note, action="insert")
    gl = _gl_of(db, per)
    target = gl if body.kind == "request" else prj.pl_user_id
    who = "requested" if body.kind == "request" else "offered"
    alerts_svc.add_alert(db, user_id=target, type="request", severity="info", person_id=per.id, project_id=prj.id, message=f"{user.name} {who} {per.name} for {prj.name} ({body.hours_per_week:g} h/week from {start:%b %-d}).", dedupe=f"request:{r.id}")
    _maybe_confirm(db, r, a, user)
    alerts_svc.recompute(db, {per.id}, actor=user.email)
    db.commit()
    return request_dict(db, r, Lookups(db), user)


def _maybe_confirm(db: Session, r: StaffingRequest, a: Allocation, user: CurrentUser) -> None:
    if r.gl_status == "accepted" and r.pl_status == "accepted" and r.status == "pending":
        r.status = "confirmed"
        audit.log(db, entity="allocation", entity_id=a.id, field="state", old=a.state, new="confirmed", actor=user.email, reason="GL and PL accepted")
        a.state = "confirmed"
        metrics.refresh_demand_status(db, a.project_id)
        if r.created_by_user_id and r.created_by_user_id != user.id:
            per, prj = db.get(Person, r.person_id), db.get(Project, r.project_id)
            alerts_svc.add_alert(db, user_id=r.created_by_user_id, type="request_confirmed", message=f"{per.name} is confirmed on {prj.name}.", person_id=per.id, project_id=prj.id, dedupe=f"confirmed:{r.id}")


class RequestAction(BaseModel):
    action: str  # accept / decline / cancel / override
    reason: str | None = None


@router.patch("/{request_id}")
def act(request_id: str, body: RequestAction, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    r = db.get(StaffingRequest, parse_uuid(request_id))
    if r is None:
        raise HTTPException(404, "request not found")
    if r.status != "pending":
        raise HTTPException(409, f"request is already {r.status}")
    per, prj = db.get(Person, r.person_id), db.get(Project, r.project_id)
    a = db.get(Allocation, r.allocation_id) if r.allocation_id else None
    is_gl, is_pl = user.is_gl_of(per), user.is_pl_of(prj)
    if body.action == "accept":
        if not (is_gl or is_pl or user.is_admin):
            raise forbid("Only the person's GL or the project's PL can accept")
        if is_gl or (user.is_admin and not is_pl):
            if r.gl_status == "pending":
                r.gl_status = "accepted"
        if is_pl or (user.is_admin and not is_gl and r.gl_status == "accepted"):
            if r.pl_status == "pending":
                r.pl_status = "accepted"
        audit.log(db, entity="request", entity_id=r.id, field="accepted_by", old=None, new=user.email, actor=user.email, reason=body.reason)
        if a:
            _maybe_confirm(db, r, a, user)
    elif body.action == "override":
        if not user.is_admin:
            raise forbid("Only an Admin can override")
        r.gl_status = r.pl_status = "accepted"
        audit.log(db, entity="request", entity_id=r.id, field="override", old=None, new="confirmed", actor=user.email, reason=body.reason or "admin override")
        if a:
            _maybe_confirm(db, r, a, user)
    elif body.action in ("decline", "cancel"):
        if body.action == "decline":
            if not (is_gl or is_pl or user.is_admin):
                raise forbid("Only the person's GL or the project's PL can decline")
            if not body.reason:
                raise HTTPException(422, "a reason is required to decline")
            if is_gl:
                r.gl_status = "declined"
            if is_pl or (user.is_admin and not is_gl):
                r.pl_status = "declined"
        elif not (user.is_admin or r.created_by_user_id == user.id):
            raise forbid("Only the requester can cancel")
        r.status = "declined" if body.action == "decline" else "cancelled"
        r.reason = body.reason
        audit.log(db, entity="request", entity_id=r.id, field="status", old="pending", new=r.status, actor=user.email, reason=body.reason)
        if a:
            audit.log(db, entity="allocation", entity_id=a.id, field="state", old=a.state, new="declined", actor=user.email, reason=body.reason)
            a.state, a.decline_reason = "declined", body.reason
            for w in a.weeks:
                w.pending_openair = False
        if r.created_by_user_id and r.created_by_user_id != user.id:
            alerts_svc.add_alert(db, user_id=r.created_by_user_id, type="request_declined", severity="warning", person_id=per.id, project_id=prj.id, message=f"{user.name} declined {per.name} for {prj.name}: {body.reason or 'no reason'}", dedupe=f"declined:{r.id}")
    else:
        raise HTTPException(422, "action must be accept, decline, cancel or override")
    r.updated_by = user.email
    alerts_svc.recompute(db, {per.id}, actor=user.email)
    db.commit()
    return request_dict(db, r, Lookups(db), user)


_ = (or_, opt_uuid)
