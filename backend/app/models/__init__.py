"""SQLAlchemy models for every table in spec section 6, plus a few supporting tables.

Every table has id (UUID), created_at, updated_at, updated_by. OpenAir-sourced tables
(people, projects, allocations) also carry source_key, row_hash, import_run_id, is_deleted.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")
HOURS = Numeric(6, 2)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(255))


class SourceMixin:
    source_key: Mapped[str | None] = mapped_column(String(255), index=True)
    row_hash: Mapped[str | None] = mapped_column(String(64))
    import_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------- users & roles
class User(TimestampMixin, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    sso_subject: Mapped[str | None] = mapped_column(String(255), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    roles: Mapped[list["UserRole"]] = relationship(back_populates="user", cascade="all, delete-orphan", lazy="selectin")


class UserRole(TimestampMixin, Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role", "scope_group_id", name="uq_user_role_scope"),)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))  # admin / sl / gl / pl / member
    scope_group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"))
    user: Mapped[User] = relationship(back_populates="roles")


class Group(TimestampMixin, Base):
    __tablename__ = "groups"
    name: Mapped[str] = mapped_column(String(255), unique=True)
    gl_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    sl_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    parent_group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"))


# ---------------------------------------------------------------- levels, rates, skills
class TechLevel(TimestampMixin, Base):
    __tablename__ = "tech_levels"
    code: Mapped[str] = mapped_column(String(20), unique=True)  # T3, T4 ...
    rank: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(String(255))


class RateCard(TimestampMixin, Base):
    __tablename__ = "rate_cards"
    tech_level_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tech_levels.id", ondelete="CASCADE"))
    role_family: Mapped[str | None] = mapped_column(String(100))
    rate_usd_hr: Mapped[float] = mapped_column(Numeric(8, 2))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)


class Skill(TimestampMixin, Base):
    __tablename__ = "skills"
    name: Mapped[str] = mapped_column(String(100), unique=True)
    category: Mapped[str | None] = mapped_column(String(100))


class PersonSkill(TimestampMixin, Base):
    __tablename__ = "person_skills"
    __table_args__ = (UniqueConstraint("person_id", "skill_id", name="uq_person_skill"),)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"))
    proficiency: Mapped[int] = mapped_column(Integer, default=1)
    skill: Mapped[Skill] = relationship(lazy="joined")


# ---------------------------------------------------------------- people
class Person(TimestampMixin, SourceMixin, Base):
    __tablename__ = "people"
    openair_user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"))
    tech_level_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tech_levels.id", ondelete="SET NULL"))
    role_family: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str | None] = mapped_column(String(100))
    std_hours_week: Mapped[float] = mapped_column(HOURS, default=40)
    manager_name: Mapped[str | None] = mapped_column(String(255))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    skills: Mapped[list[PersonSkill]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class CapacityException(TimestampMixin, Base):
    __tablename__ = "capacity_exceptions"
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    hours_off: Mapped[float] = mapped_column(HOURS)
    reason: Mapped[str | None] = mapped_column(String(255))


class AvailabilityFlag(TimestampMixin, Base):
    __tablename__ = "availability_flags"
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    from_date: Mapped[date] = mapped_column(Date)
    hours_per_week: Mapped[float] = mapped_column(HOURS)
    note: Mapped[str | None] = mapped_column(Text)
    set_by: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------------------------------------------------------------- projects & demand
class Project(TimestampMixin, SourceMixin, Base):
    __tablename__ = "projects"
    openair_project_id: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    client: Mapped[str | None] = mapped_column(String(255))
    pl_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    pl_name: Mapped[str | None] = mapped_column(String(255))  # OpenAir project_owner as text
    status: Mapped[str] = mapped_column(String(20), default="active")  # active / pipeline / on_hold / lost / closed
    billable: Mapped[bool] = mapped_column(Boolean, default=True)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(20), default="app")  # openair / app


class DemandLine(TimestampMixin, Base):
    __tablename__ = "demand_lines"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    role_family: Mapped[str | None] = mapped_column(String(100))
    min_tech_level_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tech_levels.id", ondelete="SET NULL"))
    skills: Mapped[list] = mapped_column(JSONType, default=list)
    hours_per_week: Mapped[float] = mapped_column(HOURS)
    start_week: Mapped[date] = mapped_column(Date)
    end_week: Mapped[date] = mapped_column(Date)
    confidence: Mapped[int] = mapped_column(Integer, default=100)
    requested_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open / partly / filled / cancelled
    notes: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- allocations
class Allocation(TimestampMixin, SourceMixin, Base):
    __tablename__ = "allocations"
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    demand_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("demand_lines.id", ondelete="SET NULL"))
    state: Mapped[str] = mapped_column(String(20), default="confirmed")
    source: Mapped[str] = mapped_column(String(20), default="app")
    billable: Mapped[bool] = mapped_column(Boolean, default=True)
    task_name: Mapped[str | None] = mapped_column(String(255))
    booking_type: Mapped[str | None] = mapped_column(String(20))  # Hard / Soft
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    edited_in_app: Mapped[bool] = mapped_column(Boolean, default=False)
    decline_reason: Mapped[str | None] = mapped_column(Text)
    weeks: Mapped[list["AllocationWeek"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class AllocationWeek(TimestampMixin, Base):
    __tablename__ = "allocation_weeks"
    __table_args__ = (UniqueConstraint("allocation_id", "week_start", name="uq_alloc_week"),)
    allocation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("allocations.id", ondelete="CASCADE"), index=True)
    week_start: Mapped[date] = mapped_column(Date, index=True)
    hours: Mapped[float] = mapped_column(HOURS, default=0)
    edited_in_app: Mapped[bool] = mapped_column(Boolean, default=False)
    openair_hours: Mapped[float | None] = mapped_column(HOURS)
    pending_openair: Mapped[bool] = mapped_column(Boolean, default=False)  # ALC-6


class StaffingRequest(TimestampMixin, Base):
    """A PL request for a person, or a GL offer of a person (section 2 'Who confirms a match')."""

    __tablename__ = "requests"
    kind: Mapped[str] = mapped_column(String(10))  # request / offer
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    demand_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("demand_lines.id", ondelete="SET NULL"))
    allocation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("allocations.id", ondelete="SET NULL"))
    hours_per_week: Mapped[float] = mapped_column(HOURS)
    start_week: Mapped[date] = mapped_column(Date)
    end_week: Mapped[date] = mapped_column(Date)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    gl_status: Mapped[str] = mapped_column(String(10), default="pending")  # pending/accepted/declined
    pl_status: Mapped[str] = mapped_column(String(10), default="pending")
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending/confirmed/declined/cancelled
    reason: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- ingestion
class RawFile(TimestampMixin, Base):
    __tablename__ = "raw_files"
    file_name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(512))
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20), default="upload")


class ColumnMapping(TimestampMixin, Base):
    __tablename__ = "column_mappings"
    report_type: Mapped[str] = mapped_column(String(100), unique=True)
    mapping: Mapped[dict] = mapped_column(JSONType, default=dict)


class ImportRun(TimestampMixin, Base):
    __tablename__ = "import_runs"
    source: Mapped[str] = mapped_column(String(20), default="upload")  # upload / folder / bucket / api
    report_type: Mapped[str] = mapped_column(String(100), default="openair_bookings")
    file_name: Mapped[str | None] = mapped_column(String(255))
    raw_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("raw_files.id", ondelete="SET NULL"))
    checksum: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running/succeeded/failed/skipped
    counts: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    triggered_by: Mapped[str | None] = mapped_column(String(255))


class ImportReject(TimestampMixin, Base):
    __tablename__ = "import_rejects"
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_runs.id", ondelete="CASCADE"), index=True)
    row_no: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    raw_row: Mapped[dict] = mapped_column(JSONType, default=dict)


class StgOpenAirRow(TimestampMixin, Base):
    """stg_<entity>: valid, normalised rows of one run, tagged with import_run_id."""

    __tablename__ = "stg_openair_rows"
    import_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_runs.id", ondelete="CASCADE"), index=True)
    row_no: Mapped[int] = mapped_column(Integer)
    booking_key: Mapped[str] = mapped_column(String(255))
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    row_hash: Mapped[str] = mapped_column(String(64))


class RecordHistory(TimestampMixin, Base):
    """SCD Type 2 history for OpenAir-sourced entities (people, projects, allocations)."""

    __tablename__ = "record_history"
    __table_args__ = (Index("ix_hist_entity_key", "entity", "entity_id"),)
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_key: Mapped[str | None] = mapped_column(String(255))
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    row_hash: Mapped[str | None] = mapped_column(String(64))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    import_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class ImportConflict(TimestampMixin, Base):
    """IMP-7: OpenAir changed a value that was edited in the app; the app value is kept."""

    __tablename__ = "import_conflicts"
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_runs.id", ondelete="CASCADE"), index=True)
    allocation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("allocations.id", ondelete="CASCADE"))
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    week_start: Mapped[date | None] = mapped_column(Date)
    field: Mapped[str] = mapped_column(String(64))
    app_value: Mapped[str | None] = mapped_column(Text)
    openair_value: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open / kept_app / took_openair
    gl_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


# ---------------------------------------------------------------- audit, alerts, settings
class ChangeLog(TimestampMixin, Base):
    __tablename__ = "change_log"
    __table_args__ = (Index("ix_change_entity", "entity", "entity_id"),)
    entity: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    entity_key: Mapped[str | None] = mapped_column(String(255))
    field: Mapped[str | None] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(255))  # user email or "import:<run>"
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(String(20))  # insert/update/delete


class Alert(TimestampMixin, Base):
    __tablename__ = "alerts"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    demand_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("demand_lines.id", ondelete="CASCADE"))
    week_start: Mapped[date | None] = mapped_column(Date)
    severity: Mapped[str] = mapped_column(String(10), default="info")  # info / warning / critical
    status: Mapped[str] = mapped_column(String(10), default="new")  # new / seen / done
    message: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), index=True)


class PersonWeekStatus(TimestampMixin, Base):
    """Last computed status band per person-week; used to detect status transitions for alerts."""

    __tablename__ = "person_week_status"
    __table_args__ = (UniqueConstraint("person_id", "week_start", name="uq_pws"),)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    week_start: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20))
    utilization: Mapped[float] = mapped_column(Numeric(7, 2))


class AppSetting(TimestampMixin, Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), unique=True)
    value: Mapped[dict | list | str | int | float | None] = mapped_column(JSONType)
