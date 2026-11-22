"""Derived views, recomputed from the tables (spec section 6 'Derived views').

v_person_week   -> person_weeks()
v_group_week    -> group_weeks()
v_open_demand   -> open_demand()
v_availability  -> availability()

They are computed in Python from a handful of set-based queries, so the Admin-editable bands and
confidence weighting apply without SQL changes.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Allocation,
    AllocationWeek,
    AvailabilityFlag,
    CapacityException,
    DemandLine,
    Group,
    Person,
    PersonSkill,
    Project,
    RateCard,
    Skill,
    TechLevel,
)
from app.services import settings as settings_svc
from app.services.rules import (
    PersonWeek,
    add_allocation_hours,
    effective_confidence,
    group_rollup,
    baseline_hours,
    hot_team_driver,
    is_billable,
    project_hours,
    week_range,
    week_start,
)

INACTIVE_PROJECT = ("lost", "on_hold", "closed")


def current_week() -> date:
    return week_start(date.today())


def default_window() -> tuple[date, date]:
    w = current_week()
    return w, w + timedelta(weeks=11)


def people_query(db: Session, person_ids=None, group_id=None):
    q = select(Person).where(Person.is_deleted.is_(False), Person.active.is_(True))
    if person_ids is not None:
        q = q.where(Person.id.in_(list(person_ids)))
    if group_id:
        q = q.where(Person.group_id == group_id)
    return db.scalars(q.order_by(Person.name)).all()


def person_weeks(
    db: Session,
    start: date,
    end: date,
    person_ids=None,
    group_id=None,
    cfg: dict | None = None,
    people: list[Person] | None = None,
) -> dict[uuid.UUID, dict[date, PersonWeek]]:
    cfg = cfg or settings_svc.get_all(db)
    bands, soft_w = cfg["bands"], float(cfg["soft_booking_weight"])
    weeks = week_range(start, end)
    if not weeks:
        return {}
    first, last = weeks[0], weeks[-1]
    if people is None:
        people = people_query(db, person_ids, group_id)
    ids = [p.id for p in people]
    if not ids:
        return {}

    off: dict[tuple, float] = defaultdict(float)
    for ce in db.scalars(
        select(CapacityException).where(
            CapacityException.person_id.in_(ids),
            CapacityException.date >= first,
            CapacityException.date <= last + timedelta(days=6),
        )
    ):
        off[(ce.person_id, week_start(ce.date))] += float(ce.hours_off)

    out: dict[uuid.UUID, dict[date, PersonWeek]] = {}
    for p in people:
        out[p.id] = {
            w: PersonWeek(
                person_id=str(p.id),
                week_start=w,
                capacity=max(0.0, float(p.std_hours_week or 0) - off.get((p.id, w), 0.0)),
                bands=bands,
            )
            for w in weeks
        }

    rows = db.execute(
        select(
            AllocationWeek.week_start,
            AllocationWeek.hours,
            Allocation.person_id,
            Allocation.project_id,
            Allocation.state,
            Allocation.billable,
            Allocation.booking_type,
            DemandLine.confidence,
            Project.status,
            Project.billable,
            Project.start_date,
            Project.end_date,
        )
        .join(Allocation, Allocation.id == AllocationWeek.allocation_id)
        .join(Project, Project.id == Allocation.project_id)
        .outerjoin(DemandLine, DemandLine.id == Allocation.demand_line_id)
        .where(
            Allocation.person_id.in_(ids),
            Allocation.is_deleted.is_(False),
            AllocationWeek.week_start >= first,
            AllocationWeek.week_start <= last,
        )
    ).all()
    for wk, hours, pid, prj, state, a_bill, btype, dconf, pstatus, pbill, pstart, pend in rows:
        pw = out.get(pid, {}).get(wk)
        if pw is None:
            continue
        conf = 0 if pstatus in INACTIVE_PROJECT else effective_confidence(state, dconf, btype, soft_w)
        billable = bool(a_bill) and is_billable(pstatus, pbill, pstart, pend, wk)
        add_allocation_hours(pw, str(prj), float(hours or 0), conf, billable, state)

    # Soft requests named on a demand line that have no allocation yet (DEM-3).
    allocated_lines = {
        (dl, pid)
        for dl, pid in db.execute(
            select(Allocation.demand_line_id, Allocation.person_id).where(
                Allocation.demand_line_id.is_not(None), Allocation.is_deleted.is_(False)
            )
        )
    }
    for dl, pstatus in db.execute(
        select(DemandLine, Project.status)
        .join(Project, Project.id == DemandLine.project_id)
        .where(
            DemandLine.requested_person_id.in_(ids),
            DemandLine.status != "cancelled",
            DemandLine.confidence > 0,
            DemandLine.start_week <= last,
            DemandLine.end_week >= first,
        )
    ):
        if (dl.id, dl.requested_person_id) in allocated_lines or pstatus in INACTIVE_PROJECT:
            continue
        for w in week_range(max(dl.start_week, first), min(dl.end_week, last)):
            pw = out[dl.requested_person_id].get(w)
            if pw:
                add_allocation_hours(pw, str(dl.project_id), float(dl.hours_per_week), int(dl.confidence), False, "proposed")
    return out


def group_weeks(db: Session, start: date, end: date, cfg: dict | None = None) -> list[dict]:
    cfg = cfg or settings_svc.get_all(db)
    people = people_query(db)
    pws = person_weeks(db, start, end, cfg=cfg, people=people)
    groups = {g.id: g for g in db.scalars(select(Group))}
    by_group: dict = defaultdict(list)
    for p in people:
        by_group[p.group_id].append(p.id)
    out = []
    for gid, pids in by_group.items():
        g = groups.get(gid)
        weeks = group_week_rows(pws, pids, start, end, cfg)
        out.append({"group_id": str(gid) if gid else None, "group_name": g.name if g else "Unassigned", "weeks": weeks})
    out.sort(key=lambda x: x["group_name"])
    return out


def group_week_rows(pws: dict, pids: list, start: date, end: date, cfg: dict) -> list[dict]:
    """Weekly rollups for one group, with the hot-team driver on Hot / Over-allocated weeks."""
    items_by_week = {w: [pws[pid][w] for pid in pids if pid in pws] for w in week_range(start, end)}
    baseline = baseline_hours([project_hours(items) for items in items_by_week.values()])
    rows = []
    for w, items in items_by_week.items():
        r = group_rollup(items, cfg["bands"])
        r["week_start"] = w.isoformat()
        if r["status_forecast"] in ("hot", "over"):
            r["driver"] = hot_team_driver(items, float(cfg["spike_threshold"]), baseline)
        rows.append(r)
    return rows


def demand_fill(db: Session, lines: list[DemandLine]) -> dict[uuid.UUID, dict[date, dict]]:
    """Per demand line and week: required, confirmed (filled), pending (requested/proposed) hours."""
    if not lines:
        return {}
    ids = [dl.id for dl in lines]
    filled: dict = defaultdict(lambda: defaultdict(lambda: {"confirmed": 0.0, "pending": 0.0}))
    for dl_id, state, wk, hours in db.execute(
        select(Allocation.demand_line_id, Allocation.state, AllocationWeek.week_start, AllocationWeek.hours)
        .join(AllocationWeek, AllocationWeek.allocation_id == Allocation.id)
        .where(Allocation.demand_line_id.in_(ids), Allocation.is_deleted.is_(False))
    ):
        if state == "confirmed":
            filled[dl_id][wk]["confirmed"] += float(hours)
        elif state in ("proposed", "requested"):
            filled[dl_id][wk]["pending"] += float(hours)
    out = {}
    for dl in lines:
        weeks = {}
        for w in week_range(dl.start_week, dl.end_week):
            f = filled[dl.id][w]
            req = float(dl.hours_per_week)
            weeks[w] = {
                "required": req,
                "confirmed": round(f["confirmed"], 2),
                "pending": round(f["pending"], 2),
                "unfilled": round(max(0.0, req - f["confirmed"]), 2),
            }
        out[dl.id] = weeks
    return out


def fill_status(weeks: dict[date, dict]) -> str:
    if not weeks:
        return "open"
    unfilled = [v["unfilled"] for v in weeks.values()]
    if all(u <= 0.01 for u in unfilled):
        return "filled"
    if any(v["confirmed"] > 0 for v in weeks.values()):
        return "partly"
    return "open"


def refresh_demand_status(db: Session, project_id=None) -> None:
    db.flush()
    q = select(DemandLine).where(DemandLine.status != "cancelled")
    if project_id:
        q = q.where(DemandLine.project_id == project_id)
    lines = db.scalars(q).all()
    fills = demand_fill(db, lines)
    for dl in lines:
        st = fill_status(fills.get(dl.id, {}))
        if dl.status != st:
            dl.status = st


def open_demand(db: Session, start: date, end: date, project_ids=None) -> list[dict]:
    q = (
        select(DemandLine, Project)
        .join(Project, Project.id == DemandLine.project_id)
        .where(
            DemandLine.status != "cancelled",
            DemandLine.confidence > 0,
            DemandLine.start_week <= end,
            DemandLine.end_week >= start,
            Project.is_deleted.is_(False),
            Project.status.not_in(INACTIVE_PROJECT),
        )
    )
    if project_ids is not None:
        q = q.where(DemandLine.project_id.in_(list(project_ids)))
    rows = db.execute(q).all()
    fills = demand_fill(db, [r[0] for r in rows])
    out = []
    for dl, prj in rows:
        for w, v in fills.get(dl.id, {}).items():
            if start <= w <= end and v["unfilled"] > 0:
                out.append({"demand_line_id": str(dl.id), "project_id": str(prj.id), "project_name": prj.name, "week_start": w.isoformat(), **v, "confidence": dl.confidence})
    return out


def rate_lookup(db: Session, on: date | None = None):
    """Return f(tech_level_id, role_family) -> rate for the effective rate card (role-specific wins)."""
    on = on or date.today()
    cards = db.scalars(
        select(RateCard).where(RateCard.effective_from <= on, (RateCard.effective_to.is_(None)) | (RateCard.effective_to >= on))
    ).all()
    generic, specific = {}, {}
    for c in sorted(cards, key=lambda c: c.effective_from):
        if c.role_family:
            specific[(c.tech_level_id, c.role_family.lower())] = float(c.rate_usd_hr)
        else:
            generic[c.tech_level_id] = float(c.rate_usd_hr)

    def f(level_id, role_family=None):
        if level_id is None:
            return None
        return specific.get((level_id, (role_family or "").lower()), generic.get(level_id))

    return f


def availability(
    db: Session,
    start: date,
    end: date,
    *,
    tech_level: str | None = None,
    role_family: str | None = None,
    skill: str | None = None,
    group_id=None,
    location: str | None = None,
    min_free: float = 0.0,
    cfg: dict | None = None,
) -> list[dict]:
    """v_availability: people with free hours over a week range, joined to level and skills."""
    cfg = cfg or settings_svc.get_all(db)
    levels = {t.id: t for t in db.scalars(select(TechLevel))}
    groups = {g.id: g for g in db.scalars(select(Group))}
    people = people_query(db, group_id=group_id)
    if tech_level:
        codes = {c.strip().lower() for c in tech_level.split(",") if c.strip()}
        people = [p for p in people if p.tech_level_id in levels and levels[p.tech_level_id].code.lower() in codes]
    if role_family:
        rf = {r.strip().lower() for r in role_family.split(",")}
        people = [p for p in people if (p.role_family or "").lower() in rf]
    if location:
        people = [p for p in people if (p.location or "").lower() == location.lower()]
    if skill:
        wanted = {s.strip().lower() for s in skill.split(",") if s.strip()}
        people = [p for p in people if wanted <= {ps.skill.name.lower() for ps in p.skills}]
    pws = person_weeks(db, start, end, cfg=cfg, people=people)
    flags = defaultdict(list)
    if people:
        for f in db.scalars(select(AvailabilityFlag).where(AvailabilityFlag.active.is_(True), AvailabilityFlag.person_id.in_([p.id for p in people]))):
            flags[f.person_id].append(f)
    rate = rate_lookup(db)
    out = []
    for p in people:
        weeks = pws.get(p.id, {})
        if not weeks:
            continue
        free_by_week = {}
        for w, pw in weeks.items():
            free = pw.free_hours
            for f in flags.get(p.id, []):
                if week_start(f.from_date) <= w:
                    free = max(free, float(f.hours_per_week))
            free_by_week[w] = round(free, 2)
        min_week_free = min(free_by_week.values())
        avg_free = round(sum(free_by_week.values()) / len(free_by_week), 2)
        if avg_free < min_free or (min_free <= 0 and avg_free <= 0 and not flags.get(p.id)):
            continue
        lvl = levels.get(p.tech_level_id)
        g = groups.get(p.group_id)
        statuses = [pw.status_forecast for pw in weeks.values()]
        out.append(
            {
                "person_id": str(p.id),
                "name": p.name,
                "email": p.email,
                "group_id": str(p.group_id) if p.group_id else None,
                "group_name": g.name if g else None,
                "tech_level": lvl.code if lvl else None,
                "tech_level_rank": lvl.rank if lvl else None,
                "role_family": p.role_family,
                "location": p.location,
                "skills": [{"name": ps.skill.name, "proficiency": ps.proficiency} for ps in p.skills],
                "free_hours_avg": avg_free,
                "free_hours_min": min_week_free,
                "free_by_week": {w.isoformat(): v for w, v in free_by_week.items()},
                "first_free_week": next((w.isoformat() for w, v in free_by_week.items() if v > 0), None),
                "status": max(set(statuses), key=statuses.count),
                "flags": [{"from_date": f.from_date.isoformat(), "hours_per_week": float(f.hours_per_week), "note": f.note} for f in flags.get(p.id, [])],
                "bill_rate": rate(p.tech_level_id, p.role_family),
            }
        )
    out.sort(key=lambda r: (-r["free_hours_avg"], r["name"]))
    return out


def skills_of(db: Session, person_id) -> dict[str, int]:
    return {
        s.name.lower(): ps.proficiency
        for ps, s in db.execute(select(PersonSkill, Skill).join(Skill, Skill.id == PersonSkill.skill_id).where(PersonSkill.person_id == person_id))
    }
