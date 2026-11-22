"""OpenAir ingestion with change data capture (spec section 5).

fetch -> raw_files (checksum, skip if unchanged) -> parse + map -> validate (rejects) -> stage ->
diff by business key + sha256 row_hash -> merge in one transaction with SCD2 history and change_log ->
protect in-app edits (conflicts) -> recompute affected people -> alerts -> run summary.
Re-running the same file changes nothing.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import traceback
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.ingestion import parser
from app.ingestion.storage import get_storage
from app.models import (
    Allocation,
    AllocationWeek,
    ColumnMapping,
    Group,
    ImportConflict,
    ImportReject,
    ImportRun,
    Person,
    Project,
    RawFile,
    RecordHistory,
    StgOpenAirRow,
    TechLevel,
    User,
)
from app.services import audit
from app.services.rules import spread_booking

log = logging.getLogger(__name__)
DEFAULT_REPORT = "openair_bookings"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- step 1: fetch / store raw
def store_raw(db: Session, content: bytes, file_name: str, source: str, actor: str) -> RawFile:
    checksum = hashlib.sha256(content).hexdigest()
    key = f"raw/{date.today().isoformat()}/{checksum[:12]}_{file_name}"
    get_storage().put(key, content)
    rf = RawFile(file_name=file_name, storage_key=key, checksum=checksum, size_bytes=len(content), source=source, updated_by=actor)
    db.add(rf)
    db.flush()
    return rf


def load_raw(rf: RawFile) -> bytes:
    return get_storage().get(rf.storage_key)


def saved_mapping(db: Session, report_type: str) -> dict:
    m = db.scalar(select(ColumnMapping).where(ColumnMapping.report_type == report_type))
    return dict(m.mapping) if m else {}


def save_mapping(db: Session, report_type: str, mapping: dict, actor: str) -> None:
    m = db.scalar(select(ColumnMapping).where(ColumnMapping.report_type == report_type))
    clean = {k: v for k, v in mapping.items() if v}
    if m is None:
        db.add(ColumnMapping(report_type=report_type, mapping=clean, updated_by=actor))
        audit.log(db, entity="column_mapping", entity_id=report_type, field="mapping", old=None, new=clean, actor=actor, action="insert")
    elif dict(m.mapping) != clean:
        audit.log(db, entity="column_mapping", entity_id=report_type, field="mapping", old=m.mapping, new=clean, actor=actor)
        m.mapping = clean
        m.updated_by = actor


def preview(db: Session, rf: RawFile, report_type: str = DEFAULT_REPORT) -> dict:
    df = parser.read_table(load_raw(rf), rf.file_name)
    columns = list(df.columns)
    saved = saved_mapping(db, report_type)
    mapping = parser.suggest_mapping(columns, saved)
    return {
        "raw_file_id": str(rf.id),
        "file_name": rf.file_name,
        "checksum": rf.checksum,
        "columns": columns,
        "row_count": len(df),
        "sample_rows": df.head(5).to_dict(orient="records"),
        "mapping": mapping,
        "has_saved_mapping": bool(saved),
        "missing_required": parser.missing_required(mapping),
        "fields": parser.CANONICAL_FIELDS,
    }


# ---------------------------------------------------------------- history helpers
def _open_history(db, entity, obj_id, key, data, h, run_id, deleted=False):
    db.add(
        RecordHistory(
            entity=entity, entity_id=obj_id, source_key=key, data=data, row_hash=h, valid_from=utcnow(), import_run_id=run_id, is_deleted=deleted, updated_by=f"import:{run_id}"
        )
    )


def _close_history(db, entity, obj_id):
    now = utcnow()
    for h in db.scalars(select(RecordHistory).where(RecordHistory.entity == entity, RecordHistory.entity_id == obj_id, RecordHistory.valid_to.is_(None))):
        h.valid_to = now


# ---------------------------------------------------------------- main entry
def run_import(
    raw_file_id,
    *,
    report_type: str = DEFAULT_REPORT,
    mapping: dict | None = None,
    save: bool = True,
    full_snapshot: bool = True,
    force: bool = False,
    actor: str = "system",
    source: str = "upload",
) -> uuid.UUID:
    """Run the pipeline for one stored raw file. Returns the import_run id. Never raises."""
    from app.services import alerts as alerts_svc  # local import to avoid a cycle

    # The run record lives in its own transaction so a failed merge can still report itself.
    with SessionLocal() as s:
        rf = s.get(RawFile, uuid.UUID(str(raw_file_id)))
        if rf is None:
            raise ValueError("raw file not found")
        run = ImportRun(source=source, report_type=report_type, file_name=rf.file_name, raw_file_id=rf.id, checksum=rf.checksum, status="running", triggered_by=actor, updated_by=actor)
        s.add(run)
        s.commit()
        run_id = run.id
        last = s.scalar(
            select(ImportRun)
            .where(ImportRun.report_type == report_type, ImportRun.status == "succeeded", ImportRun.id != run_id)
            .order_by(ImportRun.started_at.desc())
            .limit(1)
        )
        if last is not None and last.checksum == rf.checksum and not force:
            run.status = "skipped"
            run.counts = {**(last.counts or {}), "new": 0, "changed": 0, "removed": 0, "conflicts": 0, "alerts": 0, "message": "File is identical to the last successful load; nothing changed."}
            run.finished_at = utcnow()
            s.commit()
            return run_id

    db = SessionLocal()
    try:
        counts, affected = _merge(db, run_id, raw_file_id, report_type, mapping, save, full_snapshot, actor)
        db.commit()
    except Exception as e:  # full rollback on failure
        db.rollback()
        log.exception("import failed")
        with SessionLocal() as s:
            run = s.get(ImportRun, run_id)
            run.status = "failed"
            run.error = f"{e}\n{traceback.format_exc(limit=3)}"
            run.finished_at = utcnow()
            s.commit()
        db.close()
        return run_id

    # Step 8: recompute affected people only, then generate alerts.
    try:
        n_alerts = alerts_svc.recompute(db, affected, actor=f"import:{run_id}")
        db.commit()
    except Exception:
        db.rollback()
        log.exception("alert generation failed")
        n_alerts = 0
    run = db.get(ImportRun, run_id)
    counts["alerts"] = n_alerts
    run.counts = counts
    run.status = "succeeded"
    run.finished_at = utcnow()
    db.commit()
    db.close()
    return run_id


def _merge(db: Session, run_id, raw_file_id, report_type, mapping, save, full_snapshot, actor):
    actor_run = f"import:{run_id}"
    rf = db.get(RawFile, uuid.UUID(str(raw_file_id)))
    df = parser.read_table(load_raw(rf), rf.file_name)
    saved = saved_mapping(db, report_type)
    mapping = parser.suggest_mapping(list(df.columns), {**saved, **(mapping or {})}) if mapping is None else {**parser.suggest_mapping(list(df.columns), saved), **mapping}
    bad_cols = sorted({v for v in mapping.values() if v and v not in df.columns})
    if bad_cols:
        raise ValueError(f"Mapped columns not in the file: {', '.join(bad_cols)}")
    missing = parser.missing_required(mapping)
    if missing:
        raise ValueError(f"Column mapping incomplete: {', '.join(missing)}")
    if save:
        save_mapping(db, report_type, mapping, actor)

    levels = {t.code: t for t in db.scalars(select(TechLevel))}
    counts = defaultdict(int)
    counts["rows_read"] = len(df)

    # ---- step 2-4: normalise, validate, stage
    valid: dict[str, dict] = {}
    for i, raw in enumerate(df.to_dict(orient="records"), start=2):  # row 1 is the header
        row = parser.normalise_row(raw, mapping)
        errs = parser.validate(row, set(levels))
        key = parser.booking_key(row) if not errs else None
        if key and key in valid:
            errs.append(f"duplicate booking key {key}")
        if errs:
            db.add(ImportReject(run_id=run_id, row_no=i, reason="; ".join(errs), raw_row=raw, updated_by=actor_run))
            counts["rejected"] += 1
            continue
        row["_row_no"] = i
        row["_key"] = key
        row["_hash"] = parser.row_hash(parser.pick(row, parser.BOOKING_COLS))
        valid[key] = row
        db.add(StgOpenAirRow(import_run_id=run_id, row_no=i, booking_key=key, data=parser.pick(row, parser.BOOKING_COLS + parser.PERSON_COLS + parser.PROJECT_COLS + ["user_id", "project_id"]), row_hash=row["_hash"], updated_by=actor_run))
    db.flush()
    if counts["rows_read"] and not valid:
        raise ValueError("Every row was rejected; check the column mapping. Nothing was changed.")

    affected: set = set()
    groups = {g.name.lower(): g for g in db.scalars(select(Group))}
    users_by_name = {u.name.lower(): u for u in db.scalars(select(User))}

    # ---- people
    people_by_key = {p.source_key: p for p in db.scalars(select(Person).where(Person.source_key.is_not(None)))}
    people_by_email = {p.email.lower(): p for p in db.scalars(select(Person).where(Person.email.is_not(None)))}
    person_rows: dict[str, dict] = {}
    for r in valid.values():
        person_rows.setdefault(r["user_key"], r)
    person_of_key: dict[str, Person] = {}
    for key, r in person_rows.items():
        data = parser.pick(r, parser.PERSON_COLS)
        h = parser.row_hash(data)
        p = people_by_key.get(key) or (people_by_email.get(r["user_email"]) if r["user_email"] else None)
        grp = None
        if r["department"]:
            grp = groups.get(r["department"].lower())
            if grp is None:
                grp = Group(name=r["department"], updated_by=actor_run)
                db.add(grp)
                db.flush()
                groups[grp.name.lower()] = grp
                audit.log(db, entity="group", entity_id=grp.id, field=None, old=None, new=grp.name, actor=actor_run, run_id=run_id, action="insert")
        fields = {
            "name": r["user_name"],
            "email": r["user_email"],
            "tech_level_id": levels[r["tech_level"]].id,
            "manager_name": r["manager_name"],
            "openair_user_id": r["user_id"],
        }
        if grp is not None:
            fields["group_id"] = grp.id
        for opt in ("role_family", "location"):
            if r[opt]:
                fields[opt] = r[opt]
        if p is None:
            p = Person(source_key=key, row_hash=h, import_run_id=run_id, updated_by=actor_run, role_family=r["role_family"] or "Unassigned", **{k: v for k, v in fields.items() if k != "role_family"})
            db.add(p)
            db.flush()
            audit.log(db, entity="person", entity_id=p.id, entity_key=key, field=None, old=None, new=p.name, actor=actor_run, run_id=run_id, action="insert")
            _open_history(db, "person", p.id, key, data, h, run_id)
            counts["people_new"] += 1
            affected.add(p.id)
        elif p.row_hash != h or p.source_key != key or p.is_deleted:
            for k, v in fields.items():
                if v is None and k in ("email", "manager_name", "openair_user_id"):
                    continue
                if audit._s(getattr(p, k)) != audit._s(v):
                    audit.log(db, entity="person", entity_id=p.id, entity_key=key, field=k, old=getattr(p, k), new=v, actor=actor_run, run_id=run_id)
                    setattr(p, k, v)
            p.source_key, p.row_hash, p.import_run_id, p.is_deleted, p.updated_by = key, h, run_id, False, actor_run
            _close_history(db, "person", p.id)
            _open_history(db, "person", p.id, key, data, h, run_id)
            counts["people_changed"] += 1
            affected.add(p.id)
        person_of_key[key] = p
    db.flush()

    # ---- projects (DEM-5: link app-created pipeline projects by OpenAir ID or name)
    projects_by_key = {p.source_key: p for p in db.scalars(select(Project).where(Project.source_key.is_not(None)))}
    app_projects = list(db.scalars(select(Project).where(Project.source == "app", Project.is_deleted.is_(False))))
    project_rows: dict[str, dict] = {}
    project_dates: dict[str, list] = {}
    for r in valid.values():
        project_rows.setdefault(r["project_id"], r)
        d = project_dates.setdefault(r["project_id"], [r["start_date"], r["end_date"]])
        d[0], d[1] = min(d[0], r["start_date"]), max(d[1], r["end_date"])
    project_of_key: dict[str, Project] = {}
    for key, r in project_rows.items():
        data = parser.pick(r, parser.PROJECT_COLS)
        h = parser.row_hash(data)
        prj = projects_by_key.get(key)
        if prj is None:
            prj = next((a for a in app_projects if a.openair_project_id == key), None) or next(
                (a for a in app_projects if not a.openair_project_id and a.name.lower() == r["project_name"].lower()), None
            )
            if prj is not None:
                audit.log(db, entity="project", entity_id=prj.id, entity_key=key, field="openair_project_id", old=prj.openair_project_id, new=key, actor=actor_run, run_id=run_id, reason="linked pipeline project to OpenAir")
                prj.openair_project_id, prj.source_key, prj.source = key, key, "openair"
                counts["projects_linked"] += 1
        pl = users_by_name.get((r["project_owner"] or "").lower())
        fields = {"name": r["project_name"], "client": r["customer_name"], "pl_name": r["project_owner"], "status": r["project_stage"], "billable": r["billable"]}
        if pl is not None:
            fields["pl_user_id"] = pl.id
        if prj is None:
            s, e = project_dates[key]
            prj = Project(openair_project_id=key, source_key=key, row_hash=h, import_run_id=run_id, source="openair", start_date=s, end_date=e, updated_by=actor_run, **fields)
            db.add(prj)
            db.flush()
            audit.log(db, entity="project", entity_id=prj.id, entity_key=key, field=None, old=None, new=prj.name, actor=actor_run, run_id=run_id, action="insert")
            _open_history(db, "project", prj.id, key, data, h, run_id)
            counts["projects_new"] += 1
        elif prj.row_hash != h:
            for k, v in fields.items():
                if audit._s(getattr(prj, k)) != audit._s(v):
                    audit.log(db, entity="project", entity_id=prj.id, entity_key=key, field=k, old=getattr(prj, k), new=v, actor=actor_run, run_id=run_id)
                    setattr(prj, k, v)
            prj.row_hash, prj.import_run_id, prj.is_deleted, prj.updated_by = h, run_id, False, actor_run
            _close_history(db, "project", prj.id)
            _open_history(db, "project", prj.id, key, data, h, run_id)
            counts["projects_changed"] += 1
        s, e = project_dates[key]
        if prj.start_date is None or s < prj.start_date:
            audit.log(db, entity="project", entity_id=prj.id, field="start_date", old=prj.start_date, new=s, actor=actor_run, run_id=run_id)
            prj.start_date = s
        if prj.end_date is None or e > prj.end_date:
            audit.log(db, entity="project", entity_id=prj.id, field="end_date", old=prj.end_date, new=e, actor=actor_run, run_id=run_id)
            prj.end_date = e
        project_of_key[key] = prj
    db.flush()

    # ---- bookings -> allocations + allocation_weeks
    existing = {a.source_key: a for a in db.scalars(select(Allocation).where(Allocation.source == "openair", Allocation.source_key.is_not(None)))}
    groups_by_id = {g.id: g for g in groups.values()}

    def conflict(a: Allocation, person: Person, field: str, app_v, oa_v, week=None):
        g = groups_by_id.get(person.group_id) if person else None
        db.add(ImportConflict(run_id=run_id, allocation_id=a.id, person_id=a.person_id, week_start=week, field=field, app_value=audit._s(app_v), openair_value=audit._s(oa_v), gl_user_id=g.gl_user_id if g else None, updated_by=actor_run))
        counts["conflicts"] += 1

    for key, r in valid.items():
        person = person_of_key[r["user_key"]]
        prj = project_of_key[r["project_id"]]
        weeks = spread_booking(r["start_date"], r["end_date"], r["hours"], r["hours_per_day"])
        data = parser.pick(r, parser.BOOKING_COLS)
        a = existing.get(key)
        state = "proposed" if r["booking_type"].lower() == "soft" else "confirmed"
        if a is None or a.is_deleted:
            is_new = a is None
            if a is None:
                a = Allocation(source="openair", source_key=key, updated_by=actor_run)
                db.add(a)
            a.person_id, a.project_id, a.state, a.billable = person.id, prj.id, state, r["billable"]
            a.task_name, a.booking_type, a.start_date, a.end_date = r["task_name"], r["booking_type"], r["start_date"], r["end_date"]
            a.row_hash, a.import_run_id, a.is_deleted = r["_hash"], run_id, False
            db.flush()
            for w in list(a.weeks):
                db.delete(w)
            db.flush()
            for w, hrs in weeks.items():
                db.add(AllocationWeek(allocation_id=a.id, week_start=w, hours=hrs, openair_hours=hrs, updated_by=actor_run))
            audit.log(db, entity="allocation", entity_id=a.id, entity_key=key, field=None, old=None, new=f"{person.name} on {prj.name}: {sum(weeks.values()):g} h", actor=actor_run, run_id=run_id, action="insert")
            _open_history(db, "allocation", a.id, key, data, r["_hash"], run_id)
            counts["new"] += 1
            if not is_new:
                counts["restored"] += 1
            affected.add(person.id)
            continue
        if a.row_hash == r["_hash"]:
            continue  # same hash -> no write
        counts["changed"] += 1
        affected.update({a.person_id, person.id})
        # allocation-level fields; protect in-app edits (IMP-7)
        for field, new_v in (("person_id", person.id), ("project_id", prj.id)):
            old_v = getattr(a, field)
            if old_v != new_v:
                if a.edited_in_app:
                    conflict(a, person, field, old_v, new_v)
                else:
                    audit.log(db, entity="allocation", entity_id=a.id, entity_key=key, field=field, old=old_v, new=new_v, actor=actor_run, run_id=run_id)
                    setattr(a, field, new_v)
        for field, new_v in (("task_name", r["task_name"]), ("booking_type", r["booking_type"]), ("start_date", r["start_date"]), ("end_date", r["end_date"]), ("billable", r["billable"])):
            if audit._s(getattr(a, field)) != audit._s(new_v):
                audit.log(db, entity="allocation", entity_id=a.id, entity_key=key, field=field, old=getattr(a, field), new=new_v, actor=actor_run, run_id=run_id)
                setattr(a, field, new_v)
        if a.state in ("confirmed", "proposed") and a.state != state and not a.edited_in_app:
            audit.log(db, entity="allocation", entity_id=a.id, entity_key=key, field="state", old=a.state, new=state, actor=actor_run, run_id=run_id)
            a.state = state
        cur = {w.week_start: w for w in a.weeks}
        for wk in sorted(set(cur) | set(weeks)):
            new_h = weeks.get(wk, 0.0)
            aw = cur.get(wk)
            if aw is None:
                db.add(AllocationWeek(allocation_id=a.id, week_start=wk, hours=new_h, openair_hours=new_h, updated_by=actor_run))
                audit.log(db, entity="allocation_week", entity_id=a.id, entity_key=f"{key}@{wk}", field="hours", old=None, new=new_h, actor=actor_run, run_id=run_id)
                continue
            old_oa = float(aw.openair_hours) if aw.openair_hours is not None else float(aw.hours)
            if abs(old_oa - new_h) < 0.005:
                continue
            if aw.edited_in_app and abs(float(aw.hours) - new_h) >= 0.005:
                conflict(a, person, "hours", aw.hours, new_h, wk)  # keep the app value
                aw.openair_hours = new_h
                continue
            audit.log(db, entity="allocation_week", entity_id=a.id, entity_key=f"{key}@{wk}", field="hours", old=aw.hours, new=new_h, actor=actor_run, run_id=run_id)
            aw.hours, aw.openair_hours, aw.updated_by = new_h, new_h, actor_run
        a.row_hash, a.import_run_id, a.updated_by = r["_hash"], run_id, actor_run
        _close_history(db, "allocation", a.id)
        _open_history(db, "allocation", a.id, key, data, r["_hash"], run_id)

    # ---- soft deletes: keys in current but absent from a full snapshot
    if full_snapshot:
        for key, a in existing.items():
            if key in valid or a.is_deleted:
                continue
            person = db.get(Person, a.person_id)
            if a.edited_in_app:
                conflict(a, person, "removed_in_openair", "kept in app", "deleted")
                continue
            a.is_deleted, a.import_run_id, a.updated_by = True, run_id, actor_run
            audit.log(db, entity="allocation", entity_id=a.id, entity_key=key, field="is_deleted", old=False, new=True, actor=actor_run, run_id=run_id, action="delete")
            _close_history(db, "allocation", a.id)
            _open_history(db, "allocation", a.id, key, {}, None, run_id, deleted=True)
            counts["removed"] += 1
            affected.add(a.person_id)

    db.flush()
    from app.services.metrics import refresh_demand_status

    refresh_demand_status(db)
    for k in ("new", "changed", "removed", "rejected", "conflicts"):
        counts.setdefault(k, 0)
    counts["valid"] = len(valid)
    counts["unchanged"] = len(valid) - counts["new"] - counts["changed"]
    return dict(counts), affected


# ---------------------------------------------------------------- helpers for the API
def run_summary(db: Session, run: ImportRun) -> dict:
    c = run.counts or {}
    n_rej = db.scalar(select(func.count()).select_from(ImportReject).where(ImportReject.run_id == run.id))
    n_conf = db.scalar(select(func.count()).select_from(ImportConflict).where(ImportConflict.run_id == run.id))
    return {
        "id": str(run.id),
        "status": run.status,
        "source": run.source,
        "report_type": run.report_type,
        "file_name": run.file_name,
        "checksum": run.checksum,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "triggered_by": run.triggered_by,
        "error": run.error,
        "rows_read": c.get("rows_read", 0),
        "new": c.get("new", 0),
        "changed": c.get("changed", 0),
        "removed": c.get("removed", 0),
        "rejected": c.get("rejected", n_rej),
        "conflicts": c.get("conflicts", n_conf),
        "counts": c,
    }


def rejects_csv(db: Session, run_id) -> str:
    rows = db.scalars(select(ImportReject).where(ImportReject.run_id == run_id).order_by(ImportReject.row_no)).all()
    cols: list[str] = []
    for r in rows:
        for k in r.raw_row:
            if k not in cols:
                cols.append(k)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["row_no", "reason", *cols])
    for r in rows:
        w.writerow([r.row_no, r.reason, *[r.raw_row.get(c, "") for c in cols]])
    return buf.getvalue()
