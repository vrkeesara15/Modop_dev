"""CDC diff with the day-1 vs day-2 sample files (spec section 5)."""
from datetime import timedelta

from sqlalchemy import func, select

from app import seed
from app.db import SessionLocal
from app.ingestion import parser, pipeline
from app.models import Allocation, AllocationWeek, ChangeLog, ImportConflict, ImportReject, ImportRun, RecordHistory
from app.services import metrics


def _load(content: str, name: str, **kw):
    with SessionLocal() as db:
        rf = pipeline.store_raw(db, content.encode(), name, "upload", "test")
        db.commit()
        rid = pipeline.run_import(rf.id, actor="test", **kw)
        return db.get(ImportRun, rid)


def test_day1_is_already_loaded_and_skipped(seeded):
    run = _load(seed.sample_csv(1), "day1.csv")
    assert run.status == "skipped"
    assert run.counts["new"] == 0 and run.counts["changed"] == 0


def test_day2_applies_only_deltas_and_is_idempotent(seeded):
    with SessionLocal() as db:
        before_logs = db.scalar(select(func.count()).select_from(ChangeLog))
    run = _load(seed.sample_csv(2), "day2.csv")
    assert run.status == "succeeded", run.error
    c = run.counts
    assert (c["changed"], c["new"], c["removed"], c["rejected"]) == (14, 2, 1, 0)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ChangeLog).where(ChangeLog.run_id == run.id)) > 0
        assert db.scalar(select(func.count()).select_from(ChangeLog)) > before_logs
        removed = db.scalars(select(Allocation).where(Allocation.is_deleted.is_(True))).all()
        assert len(removed) == 1
        # SCD2: changed bookings have a closed and an open version
        a_changed = db.scalar(select(ChangeLog.entity_id).where(ChangeLog.run_id == run.id, ChangeLog.entity == "allocation_week").limit(1))
        hist = db.scalars(select(RecordHistory).where(RecordHistory.entity == "allocation", RecordHistory.entity_id == a_changed)).all()
        assert len(hist) == 2 and sum(1 for h in hist if h.valid_to is None) == 1
        logs = db.scalar(select(func.count()).select_from(ChangeLog))
    # same file again -> skipped by checksum
    assert _load(seed.sample_csv(2), "day2_again.csv").status == "skipped"
    # forced re-run -> same hashes, so no writes
    with SessionLocal() as db:
        rf = pipeline.store_raw(db, seed.sample_csv(2).encode(), "day2_forced.csv", "upload", "test")
        db.commit()
        rid = pipeline.run_import(rf.id, actor="test", force=True)
        r = db.get(ImportRun, rid)
        assert r.status == "succeeded"
        assert (r.counts["changed"], r.counts["new"], r.counts["removed"]) == (0, 0, 0)
        assert db.scalar(select(func.count()).select_from(ChangeLog)) == logs


def test_rejects_bad_rows(seeded):
    rows = seed.build_rows(metrics.current_week(), 1)
    rows[0]["hours_per_day"] = "-3"
    rows[1]["job_code"] = "Wizard"
    rows[2]["start_date"] = "not a date"
    run = _load(seed.to_csv(rows), "bad.csv", full_snapshot=False)
    assert run.status == "succeeded"
    assert run.counts["rejected"] == 3
    with SessionLocal() as db:
        reasons = [r.reason for r in db.scalars(select(ImportReject).where(ImportReject.run_id == run.id))]
        assert any("must be >= 0" in r for r in reasons)
        assert any("unknown Tech level" in r for r in reasons)
        assert any("start_date" in r for r in reasons)
        assert "row_no" in pipeline.rejects_csv(db, run.id)


def test_in_app_edit_is_protected(seeded):
    rows1 = seed.build_rows(metrics.current_week(), 1)
    rows2 = seed.build_rows(metrics.current_week(), 2)
    by1 = {r["booking_id"]: r for r in rows1}
    target = next(r for r in rows2 if r["booking_id"] in by1 and r["hours_per_day"] != by1[r["booking_id"]]["hours_per_day"])
    wk = metrics.current_week() + timedelta(weeks=1)
    with SessionLocal() as db:
        a = db.scalar(select(Allocation).where(Allocation.source_key == target["booking_id"]))
        aw = db.scalar(select(AllocationWeek).where(AllocationWeek.allocation_id == a.id, AllocationWeek.week_start == wk))
        aw.hours, aw.edited_in_app = 11, True
        a.edited_in_app = True
        db.commit()
        aid = a.id
    run = _load(seed.to_csv(rows2), "day2.csv")
    assert run.status == "succeeded"
    with SessionLocal() as db:
        aw = db.scalar(select(AllocationWeek).where(AllocationWeek.allocation_id == aid, AllocationWeek.week_start == wk))
        assert float(aw.hours) == 11  # app value kept
        assert float(aw.openair_hours) == round(float(target["hours_per_day"]) * 5, 2)
        conflicts = db.scalars(select(ImportConflict).where(ImportConflict.run_id == run.id)).all()
        assert any(c.allocation_id == aid and c.week_start == wk for c in conflicts)


def test_failed_run_rolls_back(seeded):
    rows = seed.build_rows(metrics.current_week(), 2)
    with SessionLocal() as db:
        n_alloc = db.scalar(select(func.count()).select_from(Allocation))
        rf = pipeline.store_raw(db, seed.to_csv(rows).encode(), "x.csv", "upload", "test")
        db.commit()
        rid = pipeline.run_import(rf.id, actor="test", mapping={"project_id": "nope"})
        run = db.get(ImportRun, rid)
        assert run.status == "failed"
        assert db.scalar(select(func.count()).select_from(Allocation)) == n_alloc


def test_mapping_suggestion_handles_renamed_columns():
    cols = ["Employee ID", "Resource Name", "Email", "Tech Level", "Project Number", "Project", "Start", "End", "Total Hours"]
    m = parser.suggest_mapping(cols)
    assert m["user_id"] == "Employee ID" and m["user_name"] == "Resource Name" and m["job_code"] == "Tech Level"
    assert m["project_id"] == "Project Number" and m["hours"] == "Total Hours"
    assert parser.missing_required(m) == []
    assert parser.tech_code("Tech 5") == "T5" and parser.tech_code("T6") == "T6"
