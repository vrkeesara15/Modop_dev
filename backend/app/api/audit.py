"""ADM-3 audit log (filterable, exportable) and ADM-4 Excel export of any grid or dashboard."""
import io
from datetime import date, datetime, time, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.people import change_dict
from app.auth.deps import CurrentUser, get_current_user, require_roles
from app.db import get_db
from app.models import ChangeLog

router = APIRouter(prefix="/api", tags=["audit"])


def _audit_rows(db: Session, entity, frm, to, actor, q, run_id, limit):
    qry = select(ChangeLog)
    if entity:
        qry = qry.where(ChangeLog.entity.in_(entity.split(",")))
    if frm:
        qry = qry.where(ChangeLog.created_at >= datetime.combine(frm, time.min))
    if to:
        qry = qry.where(ChangeLog.created_at < datetime.combine(to + timedelta(days=1), time.min))
    if actor:
        qry = qry.where(ChangeLog.actor.ilike(f"%{actor}%"))
    if run_id:
        qry = qry.where(ChangeLog.run_id == run_id)
    if q:
        like = f"%{q}%"
        qry = qry.where(or_(ChangeLog.entity_key.ilike(like), ChangeLog.old_value.ilike(like), ChangeLog.new_value.ilike(like), ChangeLog.reason.ilike(like), ChangeLog.field.ilike(like)))
    return [change_dict(c) for c in db.scalars(qry.order_by(ChangeLog.created_at.desc()).limit(limit))]


@router.get("/audit")
def audit_log(entity: str | None = None, frm: date | None = None, to: date | None = None, actor: str | None = None, q: str | None = None, run_id: str | None = None, limit: int = 500, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles("admin", "sl"))):
    return _audit_rows(db, entity, frm, to, actor, q, run_id, min(limit, 20000))


def _flatten(view: str, data) -> dict[str, pd.DataFrame]:
    """Turn an API payload into one or more sheets."""
    if view in ("heatmap",):
        rows = []
        for r in data["rows"]:
            row = {"name": r["name"], **({"group": r.get("group_name"), "tech_level": r.get("tech_level")} if data["level"] == "person" else {})}
            for c in r["cells"]:
                row[c["week_start"]] = f'{c["util"]:.0f}% {c["label"]}' if c["util"] is not None else c["label"]
            rows.append(row)
        return {f"heatmap_{data['mode']}": pd.DataFrame(rows)}
    if view == "grid":
        rows = [{"person": r["person_name"], "group": r["group_name"], "tech_level": r["tech_level"], "project": r["project_name"], "state": r["state"], "billable": r["billable"], **r["weeks"], "total": r["total"]} for r in data["rows"]]
        return {"allocation_grid": pd.DataFrame(rows)}
    if view == "availability":
        rows = [{"name": r["name"], "group": r["group_name"], "tech_level": r["tech_level"], "role_family": r["role_family"], "location": r["location"], "skills": ", ".join(f'{s["name"]}({s["proficiency"]})' for s in r["skills"]), "free_hours_avg": r["free_hours_avg"], **({"bill_rate": r["bill_rate"]} if "bill_rate" in r else {}), **r["free_by_week"]} for r in data["people"]]
        return {"availability": pd.DataFrame(rows)}
    if view == "leadership":
        sheets = {"billable_by_group": pd.DataFrame(data["billable_by_group"]), "supply_vs_demand": pd.DataFrame([{k: v for k, v in r.items() if k != "by_week"} for r in data["supply_demand"]])}
        rows = []
        for g in data["groups"]:
            row = {"group": g["group_name"]}
            for w in g["weeks"]:
                row[w["week_start"]] = w["util_forecast"]
            rows.append(row)
        sheets["group_heatmap"] = pd.DataFrame(rows)
        return sheets
    if view == "gl":
        return {"hot": pd.DataFrame(data["hot"]), "light": pd.DataFrame(data["light"]), "rolloffs": pd.DataFrame(data["rolloffs"]), "at_risk": pd.DataFrame(data["at_risk"])}
    if view == "pl":
        return {"projects": pd.DataFrame(data["projects"]), "unfilled_by_week": pd.DataFrame(data["unfilled_by_week"]), "at_risk_lines": pd.DataFrame(data["at_risk_lines"])}
    if isinstance(data, list):
        return {view: pd.DataFrame(data)}
    raise HTTPException(404, "unknown view")


@router.get("/export/{view}.xlsx")
def export(view: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """ADM-4: export any grid or dashboard to Excel. Query params are passed to the underlying view."""
    from app.api import allocations, dashboard, people, projects

    qp = dict(request.query_params)

    def d(k):
        return date.fromisoformat(qp[k]) if qp.get(k) else None

    if view == "heatmap":
        data = dashboard.heatmap(level=qp.get("level", "group"), mode=qp.get("mode", "forecast"), group=qp.get("group"), frm=d("frm"), to=d("to"), db=db, user=user)
    elif view == "grid":
        data = allocations.grid(group=qp.get("group"), project=qp.get("project"), person=qp.get("person"), frm=d("frm"), to=d("to"), show_released=False, db=db, user=user)
    elif view == "availability":
        data = people.list_people(group=qp.get("group"), level=qp.get("level"), skill=qp.get("skill"), role_family=qp.get("role_family"), location=qp.get("location"), q=None, available_from=d("available_from") or date.today(), available_to=d("available_to"), min_free=float(qp.get("min_free") or 0), db=db, user=user)
    elif view == "leadership":
        data = dashboard.leadership(frm=d("frm"), to=d("to"), db=db, user=user)
    elif view == "gl":
        data = dashboard.gl_dashboard(group=qp.get("group"), db=db, user=user)
    elif view == "pl":
        data = dashboard.pl_dashboard(db=db, user=user)
    elif view == "people":
        data = [{k: v for k, v in p.items() if k != "skills"} | {"skills": ", ".join(s["name"] for s in p["skills"])} for p in people.list_people(group=None, level=None, skill=None, role_family=None, location=None, q=None, available_from=None, available_to=None, min_free=0, db=db, user=user)]
    elif view == "projects":
        data = projects.list_projects(status=None, mine=False, q=None, db=db, user=user)
    elif view == "pending_openair":
        data = allocations.pending_openair(format="json", db=db, user=user)
    elif view == "audit":
        if not user.has("admin", "sl"):
            raise HTTPException(403)
        data = _audit_rows(db, qp.get("entity"), d("frm"), d("to"), qp.get("actor"), qp.get("q"), qp.get("run_id"), 20000)
    else:
        raise HTTPException(404, "unknown view")
    sheets = _flatten(view, data)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            (df if not df.empty else pd.DataFrame({"(no rows)": []})).to_excel(xw, sheet_name=name[:31], index=False)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename={view}_{date.today().isoformat()}.xlsx"})
