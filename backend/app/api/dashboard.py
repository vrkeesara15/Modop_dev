"""Dashboards (DSH-1..4) and the capacity heatmap (screen 2)."""
import uuid
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import Lookups, opt_uuid, parse_uuid, project_dict, strip_rates, window
from app.auth.deps import CurrentUser, forbid, get_current_user
from app.db import get_db
from app.models import Allocation, AllocationWeek, DemandLine, Group, ImportConflict, Person, Project, StaffingRequest, TechLevel
from app.services import metrics
from app.services import settings as settings_svc
from app.services.rules import STATUS_LABELS, STATUS_ORDER, group_rollup, hot_team_driver, week_range

router = APIRouter(prefix="/api", tags=["dashboard"])


def _project_names(db: Session) -> dict:
    return {str(p.id): p.name for p in db.scalars(select(Project))}


def _driver_named(d: dict | None, names: dict) -> dict | None:
    if not d:
        return d
    return {**d, "top_project_name": names.get(d.get("top_project_id"))}


@router.get("/dashboard/gl")
def gl_dashboard(group: str | None = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """DSH-1: own group by status band, hot vs light list, upcoming roll-offs in the next 4 weeks."""
    cfg = settings_svc.get_all(db)
    gids = {parse_uuid(group)} if group else set(user.gl_group_ids)
    if not gids and user.has("admin", "sl"):
        gids = set(db.scalars(select(Group.id)))
    if not gids:
        raise forbid("You do not lead a group")
    lk = Lookups(db)
    start = metrics.current_week()
    end = start + timedelta(weeks=11)
    people = [p for p in metrics.people_query(db) if p.group_id in gids]
    pws = metrics.person_weeks(db, start, end, cfg=cfg, people=people)
    w4 = week_range(start, start + timedelta(weeks=3))
    names = _project_names(db)
    bands_now = {s: 0 for s in STATUS_ORDER}
    hot, light, at_risk = [], [], []
    for p in people:
        weeks = pws.get(p.id, {})
        if not weeks:
            continue
        nxt = [weeks[w] for w in w4]
        bands_now[nxt[0].status_forecast] = bands_now.get(nxt[0].status_forecast, 0) + 1
        peak = max(nxt, key=lambda x: x.util_forecast or 0)
        avg = sum((x.util_forecast or 0) for x in nxt) / len(nxt)
        base = {"person_id": str(p.id), "name": p.name, "tech_level": lk.level_code(p.tech_level_id), "role_family": p.role_family, "group_name": lk.group_name(p.group_id)}
        if peak.status_forecast in ("hot", "over"):
            top = max(peak.by_project.items(), key=lambda kv: kv[1])[0] if peak.by_project else None
            hot.append({**base, "peak_util": peak.util_forecast, "peak_week": peak.week_start.isoformat(), "status": peak.status_forecast, "top_project": names.get(top)})
        if avg < cfg["bands"]["light"]:
            light.append({**base, "avg_util": round(avg, 1), "free_hours_4w": round(sum(x.free_hours for x in nxt), 1), "status": "bench" if avg < cfg["bands"]["bench"] else "light"})
        if any(x.at_risk for x in nxt):
            x = next(x for x in nxt if x.at_risk)
            at_risk.append({**base, "week": x.week_start.isoformat(), "util_confirmed": x.util_confirmed, "util_forecast": x.util_forecast})
    hot.sort(key=lambda r: -(r["peak_util"] or 0))
    light.sort(key=lambda r: r["avg_util"])
    # roll-offs: allocations whose last week with hours falls within the next 4 weeks
    horizon = start + timedelta(weeks=4)
    rolloffs = []
    ids = [p.id for p in people]
    if ids:
        last_weeks = db.execute(
            select(Allocation.person_id, Allocation.project_id, func.max(AllocationWeek.week_start))
            .join(AllocationWeek, AllocationWeek.allocation_id == Allocation.id)
            .join(Project, Project.id == Allocation.project_id)
            .where(Allocation.person_id.in_(ids), Allocation.is_deleted.is_(False), Allocation.state.in_(("confirmed", "proposed", "requested")), AllocationWeek.hours > 0, Project.status.not_in(metrics.INACTIVE_PROJECT))
            .group_by(Allocation.person_id, Allocation.project_id)
        ).all()
        last_any = defaultdict(lambda: date.min)
        for pid, _, lw in last_weeks:
            last_any[pid] = max(last_any[pid], lw)
        pmap = {p.id: p for p in people}
        for pid, prj, lw in last_weeks:
            if start <= lw < horizon:
                nxt = pws.get(pid, {}).get(lw + timedelta(weeks=1))
                rolloffs.append(
                    {
                        "person_id": str(pid),
                        "name": pmap[pid].name,
                        "project": names.get(str(prj)),
                        "last_week": lw.isoformat(),
                        "has_next_work": last_any[pid] > lw,
                        "next_week_util": nxt.util_forecast if nxt else None,
                    }
                )
    rolloffs.sort(key=lambda r: r["last_week"])
    trend = []
    by_group = defaultdict(list)
    for p in people:
        by_group[p.group_id].append(p.id)
    for gid in gids:
        weeks = metrics.group_week_rows(pws, by_group.get(gid, []), start, end, cfg)
        for r in weeks:
            if r.get("driver"):
                r["driver"] = _driver_named(r["driver"], names)
        trend.append({"group_id": str(gid), "group_name": lk.group_name(gid), "weeks": weeks})
    conflicts = db.scalar(select(func.count()).select_from(ImportConflict).where(ImportConflict.status == "open", ImportConflict.person_id.in_(ids))) if ids else 0
    pending = db.scalar(select(func.count()).select_from(StaffingRequest).where(StaffingRequest.status == "pending", StaffingRequest.person_id.in_(ids), StaffingRequest.gl_status == "pending")) if ids else 0
    return {
        "groups": [{"id": str(g), "name": lk.group_name(g)} for g in gids],
        "headcount": len(people),
        "bands_this_week": bands_now,
        "band_labels": STATUS_LABELS,
        "hot": hot,
        "light": light,
        "at_risk": at_risk,
        "rolloffs": rolloffs,
        "trend": trend,
        "open_conflicts": conflicts,
        "pending_requests": pending,
    }


@router.get("/dashboard/pl")
def pl_dashboard(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """DSH-2: own projects, unfilled demand by week, at-risk lines (low confidence or unconfirmed)."""
    lk = Lookups(db)
    q = select(Project).where(Project.is_deleted.is_(False))
    if not user.has("admin", "sl") or user.pl_project_ids:
        q = q.where(Project.id.in_(user.pl_project_ids or {uuid.uuid4()}))
    projects = db.scalars(q.order_by(Project.name)).all()
    start = metrics.current_week()
    end = start + timedelta(weeks=11)
    pids = [p.id for p in projects]
    lines = db.scalars(select(DemandLine).where(DemandLine.project_id.in_(pids), DemandLine.status != "cancelled")).all() if pids else []
    fills = metrics.demand_fill(db, lines)
    unfilled_by_week = {w.isoformat(): {"required": 0.0, "confirmed": 0.0, "unfilled": 0.0, "weighted_unfilled": 0.0} for w in week_range(start, end)}
    at_risk = []
    pnames = {p.id: p.name for p in projects}
    per_project = defaultdict(lambda: {"lines": 0, "unfilled_hours": 0.0, "at_risk": 0})
    for dl in lines:
        if dl.confidence == 0:
            continue
        for w, v in fills.get(dl.id, {}).items():
            k = w.isoformat()
            if k in unfilled_by_week:
                b = unfilled_by_week[k]
                b["required"] += v["required"]
                b["confirmed"] += v["confirmed"]
                b["unfilled"] += v["unfilled"]
                b["weighted_unfilled"] += round(v["unfilled"] * dl.confidence / 100, 2)
                per_project[dl.project_id]["unfilled_hours"] += v["unfilled"]
        per_project[dl.project_id]["lines"] += 1
        risky = dl.confidence <= 50 or dl.status in ("open", "partly")
        if risky and dl.end_week >= start:
            per_project[dl.project_id]["at_risk"] += 1
            lvl = lk.levels.get(dl.min_tech_level_id)
            at_risk.append(
                {
                    "demand_line_id": str(dl.id),
                    "project_id": str(dl.project_id),
                    "project_name": pnames[dl.project_id],
                    "role_family": dl.role_family,
                    "min_tech_level": lvl.code if lvl else None,
                    "skills": dl.skills,
                    "hours_per_week": float(dl.hours_per_week),
                    "start_week": dl.start_week.isoformat(),
                    "end_week": dl.end_week.isoformat(),
                    "confidence": dl.confidence,
                    "status": dl.status,
                    "why": ", ".join(x for x in ((f"{dl.confidence}% confidence" if dl.confidence <= 50 else ""), ("unconfirmed" if dl.status in ("open", "partly") else "")) if x),
                }
            )
    at_risk.sort(key=lambda r: r["start_week"])
    pending = db.scalar(select(func.count()).select_from(StaffingRequest).where(StaffingRequest.status == "pending", StaffingRequest.project_id.in_(pids), StaffingRequest.pl_status == "pending")) if pids else 0
    return {
        "projects": [{**project_dict(p, lk, user), **per_project[p.id], "unfilled_hours": round(per_project[p.id]["unfilled_hours"], 1)} for p in projects],
        "unfilled_by_week": [{"week_start": k, **{kk: round(vv, 1) for kk, vv in v.items()}} for k, v in unfilled_by_week.items()],
        "at_risk_lines": at_risk,
        "pending_requests": pending,
    }


@router.get("/dashboard/leadership")
def leadership(frm: date | None = None, to: date | None = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """DSH-3: company heatmap group x week, supply vs weighted demand by Tech level, billable utilization."""
    start, end = window(frm, to)
    cfg = settings_svc.get_all(db)
    names = _project_names(db)
    groups = metrics.group_weeks(db, start, end, cfg)
    for g in groups:
        for w in g["weeks"]:
            if "driver" in w:
                w["driver"] = _driver_named(w["driver"], names)
    people = metrics.people_query(db)
    pws = metrics.person_weeks(db, start, end, cfg=cfg, people=people)
    levels = sorted(db.scalars(select(TechLevel)), key=lambda t: t.rank)
    weeks = week_range(start, end)
    by_level = {t.id: {w: {"supply": 0.0, "confirmed": 0.0, "weighted_forecast": 0.0, "open_demand_weighted": 0.0, "open_demand_raw": 0.0} for w in weeks} for t in levels}
    for p in people:
        if p.tech_level_id not in by_level:
            continue
        for w, pw in pws.get(p.id, {}).items():
            b = by_level[p.tech_level_id][w]
            b["supply"] += pw.capacity
            b["confirmed"] += pw.confirmed
            b["weighted_forecast"] += pw.weighted_forecast
    for row in metrics.open_demand(db, start, end):
        dl = db.get(DemandLine, uuid.UUID(row["demand_line_id"]))
        if dl.min_tech_level_id in by_level:
            b = by_level[dl.min_tech_level_id][date.fromisoformat(row["week_start"])]
            b["open_demand_raw"] += row["unfilled"]
            b["open_demand_weighted"] += row["unfilled"] * dl.confidence / 100
    supply_demand = []
    for t in levels:
        rows = by_level[t.id]
        supply = sum(v["supply"] for v in rows.values())
        demand = sum(v["confirmed"] + v["weighted_forecast"] + v["open_demand_weighted"] for v in rows.values())
        supply_demand.append(
            {
                "tech_level": t.code,
                "supply_hours": round(supply, 1),
                "weighted_demand_hours": round(demand, 1),
                "confirmed_hours": round(sum(v["confirmed"] for v in rows.values()), 1),
                "gap_hours": round(supply - demand, 1),
                "by_week": [{"week_start": w.isoformat(), **{k: round(v, 1) for k, v in rows[w].items()}} for w in weeks],
            }
        )
    billable = []
    for g in groups:
        cap = sum(w["capacity"] for w in g["weeks"])
        bill = sum(w["billable_hours"] for w in g["weeks"])
        conf = sum(w["confirmed_hours"] for w in g["weeks"])
        billable.append({"group_id": g["group_id"], "group_name": g["group_name"], "billable_util": round(bill / cap * 100, 1) if cap else None, "confirmed_util": round(conf / cap * 100, 1) if cap else None, "billable_hours": round(bill, 1), "capacity": round(cap, 1)})
    tot_cap = sum(b["capacity"] for b in billable)
    tot_bill = sum(b["billable_hours"] for b in billable)
    return {
        "weeks": [w.isoformat() for w in weeks],
        "groups": groups,
        "supply_demand": supply_demand,
        "billable_by_group": billable,
        "company_billable_util": round(tot_bill / tot_cap * 100, 1) if tot_cap else None,
        "headcount": len(people),
    }


@router.get("/heatmap")
def heatmap(level: str = "group", mode: str = "forecast", group: str | None = None, frm: date | None = None, to: date | None = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Group or person rows x weeks, cells coloured by status band; Confirmed / Forecast toggle."""
    if mode not in ("confirmed", "forecast"):
        raise HTTPException(422, "mode must be confirmed or forecast")
    start, end = window(frm, to)
    cfg = settings_svc.get_all(db)
    weeks = [w.isoformat() for w in week_range(start, end)]
    names = _project_names(db)
    if level == "group":
        rows = []
        for g in metrics.group_weeks(db, start, end, cfg):
            cells = []
            for w in g["weeks"]:
                st = w[f"status_{mode}"]
                cells.append({"week_start": w["week_start"], "util": w[f"util_{mode}"], "status": st, "label": STATUS_LABELS[st], "capacity": w["capacity"], "confirmed_hours": w["confirmed_hours"], "weighted_forecast_hours": w["weighted_forecast_hours"], "driver": _driver_named(w.get("driver"), names)})
            rows.append({"id": g["group_id"], "name": g["group_name"], "cells": cells})
        return {"level": "group", "mode": mode, "weeks": weeks, "rows": rows, "bands": cfg["bands"]}
    lk = Lookups(db)
    people = metrics.people_query(db, group_id=opt_uuid(group))
    pws = metrics.person_weeks(db, start, end, cfg=cfg, people=people)
    rows = []
    for p in people:
        cells = []
        for w, pw in sorted(pws.get(p.id, {}).items()):
            st = pw.status(mode)
            cells.append({"week_start": w.isoformat(), "util": pw.util(mode), "status": st, "label": STATUS_LABELS[st], "capacity": pw.capacity, "confirmed_hours": round(pw.confirmed, 2), "weighted_forecast_hours": round(pw.weighted_forecast, 2), "at_risk": pw.at_risk})
        rows.append({"id": str(p.id), "name": p.name, "group_name": lk.group_name(p.group_id), "tech_level": lk.level_code(p.tech_level_id), "cells": cells})
    return {"level": "person", "mode": mode, "weeks": weeks, "rows": rows, "bands": cfg["bands"]}


@router.get("/heatmap/cell")
def heatmap_cell(week: date, person: str | None = None, group: str | None = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Click a cell -> person-week detail (or the people in a group-week)."""
    cfg = settings_svc.get_all(db)
    lk = Lookups(db)
    w = metrics.week_start(week)
    if person:
        p = db.get(Person, parse_uuid(person))
        if p is None:
            raise HTTPException(404)
        pw = metrics.person_weeks(db, w, w, people=[p], cfg=cfg)[p.id][w]
        rows = db.execute(
            select(Allocation, Project, AllocationWeek.hours)
            .join(AllocationWeek, AllocationWeek.allocation_id == Allocation.id)
            .join(Project, Project.id == Allocation.project_id)
            .where(Allocation.person_id == p.id, AllocationWeek.week_start == w, Allocation.is_deleted.is_(False))
        ).all()
        items = []
        for a, prj, hours in rows:
            visible = user.can_see_detail(p, prj, cfg["visibility_mode"])
            items.append({"allocation_id": str(a.id) if visible else None, "project_id": str(prj.id) if visible else None, "project_name": prj.name if visible else "Other commitments", "state": a.state, "hours": float(hours), "billable": bool(a.billable and prj.billable and prj.status == "active")})
        return {"person": {"id": str(p.id), "name": p.name, "group_name": lk.group_name(p.group_id), "tech_level": lk.level_code(p.tech_level_id)}, "week": pw.to_dict(), "allocations": items}
    if group:
        gid = parse_uuid(group)
        people = metrics.people_query(db, group_id=gid)
        cur = metrics.current_week()
        lo, hi = min(w, cur), max(w, cur + timedelta(weeks=11))
        pws_all = metrics.person_weeks(db, lo, hi, people=people, cfg=cfg)
        rows = {r["week_start"]: r for r in metrics.group_week_rows(pws_all, [p.id for p in people], lo, hi, cfg)}
        pws = {p.id: {w: pws_all[p.id][w]} for p in people}
        names = _project_names(db)
        items = [pws[p.id][w] for p in people]
        return {
            "group": {"id": str(gid), "name": lk.group_name(gid)},
            "week_start": w.isoformat(),
            "rollup": group_rollup(items, cfg["bands"]),
            "driver": _driver_named(rows[w.isoformat()].get("driver"), names),
            "people": sorted(({"person_id": str(p.id), "name": p.name, **pws[p.id][w].to_dict()} for p in people), key=lambda r: -(r["util_forecast"] or 0)),
        }
    raise HTTPException(422, "give person or group")


_ = strip_rates
