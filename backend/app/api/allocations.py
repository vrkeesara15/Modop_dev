"""Allocation grid, inline edits, bulk fill, reassign, history, pending OpenAir list (ALC-1..6)."""
import csv
import io
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.common import Lookups, get_or_404, opt_uuid, parse_uuid, window
from app.api.people import change_dict
from app.auth.deps import CurrentUser, forbid, get_current_user
from app.db import get_db
from app.models import Allocation, AllocationWeek, ChangeLog, DemandLine, Person, Project
from app.services import alerts as alerts_svc
from app.services import audit, metrics
from app.services import settings as settings_svc
from app.services.rules import STATUS_LABELS, week_range, week_start

router = APIRouter(prefix="/api/allocations", tags=["allocations"])
LIVE_STATES = ("confirmed", "proposed", "requested")
STATES = ("proposed", "requested", "confirmed", "released", "declined")


def _status_payload(db: Session, person_ids: set, start: date, end: date) -> dict:
    pws = metrics.person_weeks(db, start, end, person_ids=person_ids)
    return {
        str(pid): {w.isoformat(): {"util_forecast": pw.util_forecast, "util_confirmed": pw.util_confirmed, "status_forecast": pw.status_forecast, "status_confirmed": pw.status_confirmed, "capacity": pw.capacity, "label": STATUS_LABELS[pw.status_forecast]} for w, pw in weeks.items()}
        for pid, weeks in pws.items()
    }


@router.get("/grid")
def grid(
    group: str | None = None,
    project: str | None = None,
    person: str | None = None,
    frm: date | None = None,
    to: date | None = None,
    show_released: bool = False,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """ALC-1: people x project rows, week columns, editable hours, totals, utilization footer."""
    start, end = window(frm, to)
    weeks = week_range(start, end)
    cfg = settings_svc.get_all(db)
    lk = Lookups(db)
    q = (
        select(Allocation, Person, Project)
        .join(Person, Person.id == Allocation.person_id)
        .join(Project, Project.id == Allocation.project_id)
        .where(Allocation.is_deleted.is_(False), Person.is_deleted.is_(False), Person.active.is_(True))
    )
    if not show_released:
        q = q.where(Allocation.state.in_(LIVE_STATES))
    gid, prj_id, per_id = opt_uuid(group), opt_uuid(project), opt_uuid(person)
    if gid:
        q = q.where(Person.group_id == gid)
    if prj_id:
        q = q.where(Allocation.project_id == prj_id)
    if per_id:
        q = q.where(Allocation.person_id == per_id)
    if not (gid or prj_id or per_id) and not user.has("admin", "sl"):
        # default scope: own group(s) for a GL, own projects for a PL
        conds = []
        if user.gl_group_ids:
            conds.append(Person.group_id.in_(user.gl_group_ids))
        if user.pl_project_ids:
            conds.append(Allocation.project_id.in_(user.pl_project_ids))
        if conds:
            q = q.where(or_(*conds))
    rows_db = db.execute(q).all()
    dl_ids = {a.demand_line_id for a, _, _ in rows_db if a.demand_line_id}
    dl_conf = {d.id: d.confidence for d in db.scalars(select(DemandLine).where(DemandLine.id.in_(dl_ids)))} if dl_ids else {}
    rows, cands, masked, person_ids = [], [], {}, set()
    for a, per, prj in rows_db:
        wk = {w.week_start.isoformat(): float(w.hours) for w in a.weeks if start <= w.week_start <= end}
        if not any(wk.values()) and a.state == "confirmed" and not prj_id and not per_id:
            continue
        person_ids.add(per.id)
        visible = user.can_see_detail(per, prj, cfg["visibility_mode"])
        if not visible:
            m = masked.setdefault(per.id, {"row_id": f"masked-{per.id}", "allocation_id": None, "person_id": str(per.id), "person_name": per.name, "group_name": lk.group_name(per.group_id), "tech_level": lk.level_code(per.tech_level_id), "project_id": None, "project_name": "Other commitments", "client": None, "state": "confirmed", "billable": False, "editable": False, "weeks": {}, "source": None})
            for k, v in wk.items():
                m["weeks"][k] = m["weeks"].get(k, 0) + v
            continue
        cands.append(
            {
                "row_id": str(a.id),
                "allocation_id": str(a.id),
                "person_id": str(per.id),
                "person_name": per.name,
                "group_name": lk.group_name(per.group_id),
                "tech_level": lk.level_code(per.tech_level_id),
                "project_id": str(prj.id),
                "project_name": prj.name,
                "client": prj.client,
                "project_status": prj.status,
                "state": a.state,
                "confidence": 0 if prj.status in metrics.INACTIVE_PROJECT else (dl_conf.get(a.demand_line_id, 100) if a.demand_line_id else (50 if (a.booking_type or "").lower() == "soft" else 100)),
                "billable": bool(a.billable and prj.billable and prj.status == "active"),
                "source": a.source,
                "task_name": a.task_name,
                "edited_in_app": a.edited_in_app,
                "editable": user.can_edit_allocation(per, prj) and a.state not in ("released", "declined"),
                "weeks": wk,
                "_dl": str(a.demand_line_id) if a.demand_line_id else None,
            }
        )
    # Merge bookings of the same person/project/state/line into one row when their weeks don't overlap
    groups: dict = {}
    for c in cands:
        groups.setdefault((c["person_id"], c["project_id"], c["state"], c["_dl"]), []).append(c)
    for grp in groups.values():
        grp.sort(key=lambda c: -sum(c["weeks"].values()))
        merged: list[dict] = []
        for c in grp:
            busy = {k for k, v in c["weeks"].items() if v}
            tgt = next((m for m in merged if m["editable"] == c["editable"] and not (busy & {k for k, v in m["weeks"].items() if v})), None)
            if tgt is None:
                c["week_alloc"] = {k: c["allocation_id"] for k in c["weeks"]}
                c["allocation_ids"] = [c["allocation_id"]]
                c["task_names"] = [c["task_name"]] if c["task_name"] else []
                merged.append(c)
                continue
            for k, v in c["weeks"].items():
                if v or k not in tgt["weeks"]:
                    tgt["weeks"][k] = v
                    tgt["week_alloc"][k] = c["allocation_id"]
            tgt["allocation_ids"].append(c["allocation_id"])
            if c["task_name"] and c["task_name"] not in tgt["task_names"]:
                tgt["task_names"].append(c["task_name"])
            tgt["edited_in_app"] = tgt["edited_in_app"] or c["edited_in_app"]
        for m in merged:
            m.pop("_dl", None)
            m["task_name"] = ", ".join(m["task_names"]) or None
            rows.append(m)
    rows += list(masked.values())
    for r in rows:
        r["total"] = round(sum(r["weeks"].values()), 2)
    rows.sort(key=lambda r: (r["person_name"], r["project_name"]))
    status = _status_payload(db, person_ids, start, end) if person_ids else {}
    footer = {}
    for w in weeks:
        k = w.isoformat()
        cap = sum(status[p][k]["capacity"] for p in status)
        hours = sum(r["weeks"].get(k, 0) for r in rows)
        footer[k] = {"hours": round(hours, 2), "capacity": round(cap, 2), "util": round(hours / cap * 100, 1) if cap else None}
    return {"weeks": [w.isoformat() for w in weeks], "rows": rows, "person_status": status, "footer": footer}


class CellEdit(BaseModel):
    allocation_id: str
    week_start: date
    hours: float = Field(..., ge=0, le=80)


class BatchEdit(BaseModel):
    edits: list[CellEdit]
    reason: str | None = None


def _check_edit(db: Session, user: CurrentUser, a: Allocation) -> tuple[Person, Project]:
    per, prj = db.get(Person, a.person_id), db.get(Project, a.project_id)
    if not user.can_edit_allocation(per, prj):
        raise forbid(f"You cannot edit {per.name} on {prj.name}: only their GL, the project's PL or an Admin can")
    if a.state in ("released", "declined"):
        raise HTTPException(409, "Allocation is released/declined")
    return per, prj


def set_week(db: Session, a: Allocation, wk: date, hours: float, actor: str, reason: str | None) -> bool:
    wk = week_start(wk)
    aw = next((w for w in a.weeks if w.week_start == wk), None)
    old = float(aw.hours) if aw else 0.0
    if abs(old - hours) < 0.005:
        return False
    if aw is None:
        aw = AllocationWeek(allocation_id=a.id, week_start=wk, hours=hours, edited_in_app=True, pending_openair=True, updated_by=actor)
        a.weeks.append(aw)
    else:
        aw.hours, aw.edited_in_app, aw.pending_openair, aw.updated_by = round(hours, 2), True, True, actor
    a.edited_in_app, a.updated_by = True, actor
    audit.log(db, entity="allocation_week", entity_id=a.id, entity_key=f"{a.source_key or a.id}@{wk}", field="hours", old=old, new=hours, actor=actor, reason=reason)
    return True


def _finish(db: Session, user: CurrentUser, people: set, weeks: set) -> dict:
    db.flush()
    metrics.refresh_demand_status(db)
    n = alerts_svc.recompute(db, people, actor=user.email)
    db.commit()
    if not weeks:
        return {"person_status": {}, "alerts_raised": n}
    return {"person_status": _status_payload(db, people, min(weeks), max(weeks)), "alerts_raised": n}


@router.patch("/weeks")
def patch_weeks(body: BatchEdit, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Batch cell edits [{allocation_id, week_start, hours}]; returns recomputed status."""
    people, weeks, changed = set(), set(), 0
    cache: dict = {}
    for e in body.edits:
        aid = parse_uuid(e.allocation_id, "allocation_id")
        a = cache.get(aid) or db.get(Allocation, aid)
        if a is None or a.is_deleted:
            raise HTTPException(404, f"allocation {aid} not found")
        cache[aid] = a
        _check_edit(db, user, a)
        if set_week(db, a, e.week_start, e.hours, user.email, body.reason):
            changed += 1
        people.add(a.person_id)
        weeks.add(week_start(e.week_start))
    out = _finish(db, user, people, weeks)
    out["changed"] = changed
    return out


class BulkFill(BaseModel):
    allocation_id: str
    frm: date
    to: date
    hours: float = Field(..., ge=0, le=80)
    reason: str | None = None
    weekdays_only: bool = True
    allocation_ids: list[str] | None = None  # a merged grid row: each week goes to the booking that already holds it


@router.post("/bulk")
def bulk_fill(body: BulkFill, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """ALC-2: e.g. 'set 20 h/week from Dec 1 to Jan 15' (every week touching the range)."""
    a = get_or_404(db, Allocation, body.allocation_id)
    _check_edit(db, user, a)
    if body.to < body.frm:
        raise HTTPException(422, "'to' before 'from'")
    weeks = week_range(body.frm, body.to)
    if len(weeks) > 80:
        raise HTTPException(422, "range too large")
    allocs = [a]
    for x in body.allocation_ids or []:
        b = get_or_404(db, Allocation, x)
        if b.id != a.id and b.person_id == a.person_id and b.project_id == a.project_id:
            _check_edit(db, user, b)
            allocs.append(b)

    def owner(w: date) -> Allocation:
        return next((b for b in allocs if any(x.week_start == w and float(x.hours) > 0 for x in b.weeks)), a)

    reason = body.reason or f"bulk fill {body.hours:g} h/week {body.frm}..{body.to}"
    changed = sum(set_week(db, owner(w), w, body.hours, user.email, reason) for w in weeks)
    out = _finish(db, user, {a.person_id}, set(weeks))
    out["changed"] = changed
    return out


class NewAllocation(BaseModel):
    person_id: str
    project_id: str
    demand_line_id: str | None = None
    hours_per_week: float = Field(..., ge=0, le=80)
    frm: date
    to: date
    billable: bool | None = None
    reason: str | None = None


@router.post("")
def create_allocation(body: NewAllocation, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Add a person to a project directly. GLs (own people) and Admins confirm directly; PLs use requests."""
    per = get_or_404(db, Person, body.person_id)
    prj = get_or_404(db, Project, body.project_id)
    if not (user.is_admin or user.is_gl_of(per)):
        raise forbid("Only the person's GL or an Admin can allocate directly; PLs send a request instead")
    a = Allocation(person_id=per.id, project_id=prj.id, demand_line_id=opt_uuid(body.demand_line_id), state="confirmed", source="app", billable=prj.billable if body.billable is None else body.billable, start_date=body.frm, end_date=body.to, edited_in_app=True, updated_by=user.email)
    db.add(a)
    db.flush()
    audit.log(db, entity="allocation", entity_id=a.id, field=None, old=None, new=f"{per.name} on {prj.name}: {body.hours_per_week:g} h/week {body.frm}..{body.to}", actor=user.email, reason=body.reason, action="insert")
    weeks = week_range(body.frm, body.to)
    for w in weeks:
        set_week(db, a, w, body.hours_per_week, user.email, body.reason)
    out = _finish(db, user, {per.id}, set(weeks))
    out["allocation_id"] = str(a.id)
    return out


class Reassign(BaseModel):
    allocation_id: str
    to_project_id: str | None = None
    to_person_id: str | None = None
    frm: date | None = None
    to: date | None = None
    reason: str | None = None


@router.post("/reassign")
def reassign(body: Reassign, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """ALC-3: move a person's hours from project A to B, or a line from person X to Y, in one action."""
    src = get_or_404(db, Allocation, body.allocation_id)
    per, prj = _check_edit(db, user, src)
    if bool(body.to_project_id) == bool(body.to_person_id):
        raise HTTPException(422, "give exactly one of to_project_id or to_person_id")
    tgt_person = get_or_404(db, Person, body.to_person_id) if body.to_person_id else per
    tgt_project = get_or_404(db, Project, body.to_project_id) if body.to_project_id else prj
    if tgt_person.id == per.id and tgt_project.id == prj.id:
        raise HTTPException(422, "target is the same as the source")
    if not user.can_edit_allocation(tgt_person, tgt_project):
        raise forbid("You cannot allocate to the target")
    start = week_start(body.frm) if body.frm else min((w.week_start for w in src.weeks), default=metrics.current_week())
    end = week_start(body.to) if body.to else max((w.week_start for w in src.weeks), default=start)
    tgt = db.scalar(
        select(Allocation).where(Allocation.person_id == tgt_person.id, Allocation.project_id == tgt_project.id, Allocation.is_deleted.is_(False), Allocation.state.in_(LIVE_STATES))
    )
    if tgt is None:
        tgt = Allocation(
            person_id=tgt_person.id, project_id=tgt_project.id, demand_line_id=src.demand_line_id if tgt_project.id == prj.id else None, state=src.state, source="app",
            billable=tgt_project.billable, task_name=src.task_name, edited_in_app=True, updated_by=user.email,
        )
        db.add(tgt)
        db.flush()
        audit.log(db, entity="allocation", entity_id=tgt.id, field=None, old=None, new=f"{tgt_person.name} on {tgt_project.name} (reassigned)", actor=user.email, reason=body.reason, action="insert")
    reason = body.reason or f"reassign {per.name}/{prj.name} -> {tgt_person.name}/{tgt_project.name}"
    moved, weeks = 0.0, set()
    for w in list(src.weeks):
        if start <= w.week_start <= end and float(w.hours) > 0:
            h = float(w.hours)
            cur = next((x for x in tgt.weeks if x.week_start == w.week_start), None)
            set_week(db, tgt, w.week_start, (float(cur.hours) if cur else 0) + h, user.email, reason)
            set_week(db, src, w.week_start, 0, user.email, reason)
            moved += h
            weeks.add(w.week_start)
    audit.log(db, entity="allocation", entity_id=src.id, field="reassign", old=f"{per.name} / {prj.name}", new=f"{tgt_person.name} / {tgt_project.name}: {moved:g} h", actor=user.email, reason=reason)
    out = _finish(db, user, {per.id, tgt_person.id}, weeks)
    out.update({"moved_hours": round(moved, 2), "target_allocation_id": str(tgt.id)})
    return out


class StatePatch(BaseModel):
    state: str
    reason: str | None = None


@router.patch("/{allocation_id}")
def patch_state(allocation_id: str, body: StatePatch, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """ALC-5 transitions (Proposed -> Requested -> Confirmed -> Released; Declined with reason)."""
    a = get_or_404(db, Allocation, allocation_id)
    per, prj = db.get(Person, a.person_id), db.get(Project, a.project_id)
    if body.state not in STATES:
        raise HTTPException(422, f"state must be one of {STATES}")
    if body.state == "confirmed" and not (user.is_admin or (user.is_gl_of(per) and user.is_pl_of(prj))):
        raise forbid("Confirmation needs both the GL and PL (use the requests inbox) or an Admin override")
    if not user.can_edit_allocation(per, prj):
        raise forbid()
    if body.state == "declined" and not body.reason:
        raise HTTPException(422, "a reason is required to decline")
    audit.log(db, entity="allocation", entity_id=a.id, field="state", old=a.state, new=body.state, actor=user.email, reason=body.reason)
    a.state, a.edited_in_app, a.updated_by = body.state, True, user.email
    if body.state == "declined":
        a.decline_reason = body.reason
    for w in a.weeks:
        w.pending_openair = True
    out = _finish(db, user, {a.person_id}, {w.week_start for w in a.weeks})
    out["state"] = a.state
    return out


@router.get("/{allocation_id}/history")
def history(allocation_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    a = db.get(Allocation, parse_uuid(allocation_id))
    if a is None:
        raise HTTPException(404)
    rows = db.scalars(select(ChangeLog).where(ChangeLog.entity_id == str(a.id)).order_by(ChangeLog.created_at.desc()).limit(500)).all()
    return [change_dict(c) for c in rows]


@router.get("/pending-openair")
def pending_openair(format: str = "json", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """ALC-6: in-app changes not yet keyed into OpenAir, exportable as CSV."""
    rows = db.execute(
        select(AllocationWeek, Allocation, Person, Project)
        .join(Allocation, Allocation.id == AllocationWeek.allocation_id)
        .join(Person, Person.id == Allocation.person_id)
        .join(Project, Project.id == Allocation.project_id)
        .where(AllocationWeek.pending_openair.is_(True))
        .order_by(Person.name, Project.name, AllocationWeek.week_start)
    ).all()
    out = [
        {
            "id": str(w.id),
            "person": p.name,
            "openair_user_id": p.openair_user_id,
            "project": prj.name,
            "openair_project_id": prj.openair_project_id,
            "booking_id": a.source_key,
            "state": a.state,
            "week_start": w.week_start.isoformat(),
            "app_hours": float(w.hours) if a.state not in ("released", "declined") else 0.0,
            "openair_hours": float(w.openair_hours) if w.openair_hours is not None else None,
            "changed_by": w.updated_by,
            "changed_at": w.updated_at.isoformat() if w.updated_at else None,
        }
        for w, a, p, prj in rows
        if user.can_see_detail(p, prj, "open") or user.is_admin
    ]
    out = [r for r in out if r["openair_hours"] is None or abs((r["openair_hours"] or 0) - r["app_hours"]) > 0.005]
    if format == "csv":
        buf = io.StringIO()
        if out:
            w = csv.DictWriter(buf, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=pending_openair_updates.csv"})
    return out


class MarkKeyed(BaseModel):
    ids: list[str]


@router.post("/pending-openair/mark-keyed")
def mark_keyed(body: MarkKeyed, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if not user.has("admin", "gl"):
        raise forbid()
    n = 0
    for i in body.ids:
        w = db.get(AllocationWeek, parse_uuid(i))
        if w and w.pending_openair:
            w.pending_openair = False
            audit.log(db, entity="allocation_week", entity_id=w.allocation_id, field="pending_openair", old=True, new=False, actor=user.email, reason="keyed into OpenAir")
            n += 1
    db.commit()
    return {"marked": n}


_ = (uuid, timedelta)
