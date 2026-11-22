"""Alert generation (ALR-1, ALR-2), run after every import and every edit.

Rules
- a person enters a status band that alerts someone: Bench / Light -> GL; Hot -> GL + PL;
  Over-allocated -> GL + PL + SL. (Healthy never alerts.) Only the first week of each episode
  is alerted, and only when the band changed since the last computation.
- an open demand line is unfilled 2 weeks before it starts -> PL (25% lines are shown, not alerted).
- a project goes to 0% -> the GLs of the people attached (raised from the demand API).
- a person rolls off within 2 weeks with no next work -> GL.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alert, Allocation, AllocationWeek, DemandLine, Group, Person, PersonWeekStatus, Project
from app.services import metrics
from app.services import settings as settings_svc
from app.services.rules import STATUS_LABELS, week_start

ALERTING = {"bench": "warning", "light": "info", "hot": "warning", "over": "critical"}
HORIZON_WEEKS = 12


def _fmt(d: date) -> str:
    return d.strftime("%b %-d")


def add_alert(db: Session, *, user_id, type: str, message: str, severity: str = "info", person_id=None, project_id=None, demand_line_id=None, week=None, dedupe: str | None = None) -> bool:
    if user_id is None:
        return False
    key = f"{user_id}:{dedupe}" if dedupe else None
    if key and db.scalar(select(Alert.id).where(Alert.dedupe_key == key, Alert.status != "done").limit(1)):
        return False
    db.add(
        Alert(
            user_id=user_id, type=type, message=message, severity=severity, person_id=person_id, project_id=project_id, demand_line_id=demand_line_id, week_start=week, dedupe_key=key, updated_by="system"
        )
    )
    return True


def recompute(db: Session, person_ids=None, actor: str = "system") -> int:
    """Recompute status for the affected people (None = everyone) and raise alerts. Returns alerts added."""
    db.flush()
    cfg = settings_svc.get_all(db)
    start = metrics.current_week()
    end = start + timedelta(weeks=HORIZON_WEEKS - 1)
    people = metrics.people_query(db, person_ids=person_ids)
    if person_ids is not None and not person_ids:
        people = []
    pws = metrics.person_weeks(db, start, end, cfg=cfg, people=people)
    groups = {g.id: g for g in db.scalars(select(Group))}
    projects = {p.id: p for p in db.scalars(select(Project))}
    ids = [p.id for p in people]
    prev = {}
    if ids:
        prev = {(s.person_id, s.week_start): s for s in db.scalars(select(PersonWeekStatus).where(PersonWeekStatus.person_id.in_(ids), PersonWeekStatus.week_start >= start))}
    n = 0
    for p in people:
        g = groups.get(p.group_id)
        weeks = pws.get(p.id, {})
        last_status = None
        for w in sorted(weeks):
            pw = weeks[w]
            st, util = pw.status_forecast, pw.util_forecast or 0
            old = prev.get((p.id, w))
            changed = old is None or old.status != st
            if old is None:
                db.add(PersonWeekStatus(person_id=p.id, week_start=w, status=st, utilization=util, updated_by=actor))
            else:
                old.status, old.utilization = st, util
            if st in ALERTING and changed and st != last_status:
                label = STATUS_LABELS[st]
                msg = f"{p.name} is {label} in the week of {_fmt(w)} ({util:.0f}% forecast, {pw.util_confirmed or 0:.0f}% confirmed)."
                dd = f"status:{p.id}:{w}:{st}"
                recipients = {g.gl_user_id} if g else set()
                if st in ("hot", "over"):
                    for prj_id in pw.by_project:
                        prj = projects.get(uuid.UUID(prj_id))
                        if prj and prj.pl_user_id:
                            recipients.add(prj.pl_user_id)
                if st == "over" and g and g.sl_user_id:
                    recipients.add(g.sl_user_id)
                for uid in recipients:
                    n += add_alert(db, user_id=uid, type=f"status_{st}", message=msg, severity=ALERTING[st], person_id=p.id, week=w, dedupe=dd)
            last_status = st
        n += _rolloff(db, p, g, start, cfg)
    n += unfilled_demand(db, cfg)
    return n


def _rolloff(db: Session, p: Person, g: Group | None, start: date, cfg: dict) -> int:
    """Roll-off within N days with no next work."""
    if g is None or g.gl_user_id is None:
        return 0
    horizon = start + timedelta(days=int(cfg.get("rolloff_alert_days", 14)))
    rows = db.execute(
        select(AllocationWeek.week_start, Project.name, Allocation.state)
        .join(Allocation, Allocation.id == AllocationWeek.allocation_id)
        .join(Project, Project.id == Allocation.project_id)
        .where(
            Allocation.person_id == p.id,
            Allocation.is_deleted.is_(False),
            Allocation.state.in_(("confirmed", "proposed", "requested")),
            Project.status.not_in(metrics.INACTIVE_PROJECT),
            AllocationWeek.hours > 0,
            AllocationWeek.week_start >= start,
        )
    ).all()
    if not rows:
        return 0
    last = max(r[0] for r in rows)
    if last > horizon:
        return 0
    proj = sorted({r[1] for r in rows if r[0] == last})
    msg = f"{p.name} rolls off {', '.join(proj)} after the week of {_fmt(last)} with no next work."
    return add_alert(db, user_id=g.gl_user_id, type="rolloff", message=msg, severity="warning", person_id=p.id, week=last, dedupe=f"rolloff:{p.id}:{last}")


def unfilled_demand(db: Session, cfg: dict) -> int:
    today = date.today()
    soon = today + timedelta(days=int(cfg.get("unfilled_alert_days", 14)))
    lines = db.execute(
        select(DemandLine, Project)
        .join(Project, Project.id == DemandLine.project_id)
        .where(
            DemandLine.status.in_(("open", "partly")),
            DemandLine.confidence >= 50,
            DemandLine.start_week <= soon,
            DemandLine.end_week >= week_start(today),
            Project.status.not_in(metrics.INACTIVE_PROJECT),
        )
    ).all()
    n = 0
    for dl, prj in lines:
        if not prj.pl_user_id:
            continue
        msg = f"Demand on {prj.name} ({dl.role_family or 'any role'}, {float(dl.hours_per_week):g} h/week from {_fmt(dl.start_week)}) is still unfilled."
        n += add_alert(db, user_id=prj.pl_user_id, type="demand_unfilled", message=msg, severity="warning", project_id=prj.id, demand_line_id=dl.id, week=dl.start_week, dedupe=f"unfilled:{dl.id}")
    return n


def project_zero(db: Session, project: Project, people: list[Person], actor: str) -> int:
    """A project (or line) went to 0%: notify the GLs of the people attached."""
    groups = {g.id: g for g in db.scalars(select(Group))}
    by_gl = defaultdict(list)
    for p in people:
        g = groups.get(p.group_id)
        if g and g.gl_user_id:
            by_gl[g.gl_user_id].append(p.name)
    n = 0
    for gl, names in by_gl.items():
        msg = f"{project.name} moved to 0% (lost / on hold). Released: {', '.join(sorted(names))}."
        n += add_alert(db, user_id=gl, type="project_zero", message=msg, severity="warning", project_id=project.id, dedupe=f"zero:{project.id}:{date.today()}:{len(names)}")
    return n
