"""Imports screen API: upload, column mapping, run, history, summary, rejects, conflicts (IMP-1..7)."""
import json
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import parse_uuid
from app.auth.deps import CurrentUser, forbid, get_current_user, require_roles
from app.db import get_db
from app.ingestion import parser, pipeline, sources
from app.models import Allocation, AllocationWeek, ColumnMapping, ImportConflict, ImportRun, Person, Project, RawFile
from app.services import alerts as alerts_svc
from app.services import audit

router = APIRouter(prefix="/api/imports", tags=["imports"])
admin_only = require_roles("admin")
MAX_UPLOAD = 50 * 1024 * 1024


@router.get("/fields")
def fields(user: CurrentUser = Depends(admin_only)):
    return parser.CANONICAL_FIELDS


@router.get("/mappings")
def mappings(db: Session = Depends(get_db), user: CurrentUser = Depends(admin_only)):
    return [{"report_type": m.report_type, "mapping": m.mapping, "updated_at": m.updated_at.isoformat(), "updated_by": m.updated_by} for m in db.scalars(select(ColumnMapping))]


@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    report_type: str = Form(pipeline.DEFAULT_REPORT),
    auto_run: bool = Form(True),
    full_snapshot: bool = Form(True),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(admin_only),
):
    """IMP-1/2: store the raw file; if a saved (or fully auto-detected) mapping covers it, load it right away."""
    content = await file.read()
    if not content:
        raise HTTPException(422, "empty file")
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "file too large")
    name = (file.filename or "upload.csv").replace("/", "_").replace("\\", "_")
    if not name.lower().endswith((".csv", ".xlsx", ".xls", ".xlsm", ".txt")):
        raise HTTPException(422, "upload a CSV or XLSX file")
    rf = pipeline.store_raw(db, content, name, "upload", user.email)
    db.commit()
    try:
        pv = pipeline.preview(db, rf, report_type)
    except Exception as e:
        raise HTTPException(422, f"could not read the file: {e}")
    if auto_run and pv["has_saved_mapping"] and not pv["missing_required"]:
        run_id = pipeline.run_import(rf.id, report_type=report_type, mapping=None, full_snapshot=full_snapshot, actor=user.email)
        db.expire_all()
        pv["run"] = pipeline.run_summary(db, db.get(ImportRun, run_id))
    return pv


class RunIn(BaseModel):
    raw_file_id: str | None = None
    report_type: str = pipeline.DEFAULT_REPORT
    mapping: dict | None = None
    save_mapping: bool = True
    full_snapshot: bool = True
    force: bool = False


@router.post("/run")
def run(body: RunIn | None = None, background: BackgroundTasks = None, db: Session = Depends(get_db), user: CurrentUser = Depends(admin_only)):
    """Run the pipeline for an uploaded file with a (confirmed) mapping, or with no file trigger the
    scheduled pull (watched folder / bucket / OpenAir API) - the endpoint Cloud Scheduler calls."""
    body = body or RunIn()
    if not body.raw_file_id:
        runs = sources.scheduled_pull(actor=user.email)
        return {"runs": [pipeline.run_summary(db, db.get(ImportRun, uuid.UUID(r))) for r in runs], "message": None if runs else "No new files in the inbox and no OpenAir API configured."}
    rf = db.get(RawFile, parse_uuid(body.raw_file_id))
    if rf is None:
        raise HTTPException(404, "raw file not found")
    if body.mapping is not None:
        unknown = set(body.mapping) - set(parser.FIELD_NAMES)
        if unknown:
            raise HTTPException(422, f"unknown fields in mapping: {sorted(unknown)}")
        miss = parser.missing_required({k: v for k, v in body.mapping.items() if v})
        if miss:
            raise HTTPException(422, f"mapping incomplete: {', '.join(miss)}")
    run_id = pipeline.run_import(rf.id, report_type=body.report_type, mapping=body.mapping, save=body.save_mapping, full_snapshot=body.full_snapshot, force=body.force, actor=user.email)
    db.expire_all()
    return pipeline.run_summary(db, db.get(ImportRun, run_id))


@router.get("")
def list_runs(limit: int = 50, db: Session = Depends(get_db), user: CurrentUser = Depends(admin_only)):
    return [pipeline.run_summary(db, r) for r in db.scalars(select(ImportRun).order_by(ImportRun.started_at.desc()).limit(limit))]


@router.get("/samples/{name}")
def sample(name: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Download the generated sample OpenAir exports (anchored to the demo seed's week, else the current week)."""
    from datetime import date as _date

    from app import seed
    from app.models import AppSetting

    if name not in ("openair_export_day1.csv", "openair_export_day2.csv"):
        raise HTTPException(404)
    row = db.scalar(select(AppSetting).where(AppSetting.key == "demo_anchor"))
    anchor = _date.fromisoformat(row.value) if row is not None and isinstance(row.value, str) else None
    content = seed.sample_csv(day=1 if "day1" in name else 2, anchor=anchor)
    return Response(content, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={name}"})


@router.get("/conflicts")
def conflicts(status: str = "open", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """IMP-7 conflict queue: Admins see all, GLs see their people's."""
    if not user.has("admin", "gl"):
        raise forbid()
    q = select(ImportConflict, Person, Allocation, Project).outerjoin(Person, Person.id == ImportConflict.person_id).outerjoin(Allocation, Allocation.id == ImportConflict.allocation_id).outerjoin(Project, Project.id == Allocation.project_id)
    if status != "all":
        q = q.where(ImportConflict.status == status)
    out = []
    for c, p, a, prj in db.execute(q.order_by(ImportConflict.created_at.desc())).all():
        if not (user.is_admin or user.is_gl_of(p)):
            continue
        out.append({
            "id": str(c.id), "run_id": str(c.run_id), "allocation_id": str(c.allocation_id) if c.allocation_id else None,
            "person_id": str(p.id) if p else None, "person_name": p.name if p else None, "project_name": prj.name if prj else None,
            "booking_id": a.source_key if a else None, "week_start": c.week_start.isoformat() if c.week_start else None,
            "field": c.field, "app_value": c.app_value, "openair_value": c.openair_value, "status": c.status, "created_at": c.created_at.isoformat(),
        })
    return out


class Resolve(BaseModel):
    resolution: str  # keep_app / take_openair


@router.patch("/conflicts/{conflict_id}")
def resolve(conflict_id: str, body: Resolve, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    c = db.get(ImportConflict, parse_uuid(conflict_id))
    if c is None:
        raise HTTPException(404)
    p = db.get(Person, c.person_id) if c.person_id else None
    if not (user.is_admin or user.is_gl_of(p)):
        raise forbid()
    if c.status != "open":
        raise HTTPException(409, "already resolved")
    if body.resolution not in ("keep_app", "take_openair"):
        raise HTTPException(422)
    a = db.get(Allocation, c.allocation_id) if c.allocation_id else None
    if body.resolution == "take_openair" and a is not None:
        if c.field == "hours" and c.week_start:
            aw = db.scalar(select(AllocationWeek).where(AllocationWeek.allocation_id == a.id, AllocationWeek.week_start == c.week_start))
            new = float(c.openair_value or 0)
            if aw:
                audit.log(db, entity="allocation_week", entity_id=a.id, entity_key=f"{a.source_key}@{c.week_start}", field="hours", old=aw.hours, new=new, actor=user.email, reason="conflict resolved: take OpenAir")
                aw.hours, aw.edited_in_app, aw.pending_openair = new, False, False
        elif c.field in ("person_id", "project_id"):
            audit.log(db, entity="allocation", entity_id=a.id, field=c.field, old=getattr(a, c.field), new=c.openair_value, actor=user.email, reason="conflict resolved: take OpenAir")
            setattr(a, c.field, uuid.UUID(c.openair_value))
        elif c.field == "removed_in_openair":
            audit.log(db, entity="allocation", entity_id=a.id, field="is_deleted", old=False, new=True, actor=user.email, reason="conflict resolved: take OpenAir")
            a.is_deleted = True
    c.status = "kept_app" if body.resolution == "keep_app" else "took_openair"
    c.updated_by = user.email
    audit.log(db, entity="import_conflict", entity_id=c.id, field="status", old="open", new=c.status, actor=user.email)
    if a:
        alerts_svc.recompute(db, {a.person_id}, actor=user.email)
    db.commit()
    return {"ok": True, "status": c.status}


@router.get("/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(admin_only)):
    r = db.get(ImportRun, parse_uuid(run_id))
    if r is None:
        raise HTTPException(404)
    from app.models import ChangeLog, ImportReject

    d = pipeline.run_summary(db, r)
    d["rejects"] = [{"row_no": x.row_no, "reason": x.reason, "raw_row": x.raw_row} for x in db.scalars(select(ImportReject).where(ImportReject.run_id == r.id).order_by(ImportReject.row_no).limit(200))]
    d["changes"] = [
        {"entity": c.entity, "entity_key": c.entity_key, "field": c.field, "old_value": c.old_value, "new_value": c.new_value, "action": c.action}
        for c in db.scalars(select(ChangeLog).where(ChangeLog.run_id == r.id).order_by(ChangeLog.created_at).limit(500))
    ]
    return d


@router.get("/{run_id}/rejects.csv")
def rejects(run_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(admin_only)):
    return Response(pipeline.rejects_csv(db, parse_uuid(run_id)), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=rejects_{run_id[:8]}.csv"})


_ = json
