"""Parse, map, normalise and validate OpenAir export rows (pipeline steps 2 and 3)."""
from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import date, datetime

import pandas as pd

from app.services.rules import week_start

# Canonical fields of the "openair_bookings" report type (spec 'Assumed OpenAir export columns').
CANONICAL_FIELDS: list[dict] = [
    {"field": "user_id", "label": "OpenAir user ID", "required": False, "aliases": ["user id", "userid", "employee id", "resource id"]},
    {"field": "user_name", "label": "Person name", "required": True, "aliases": ["user", "name", "employee", "resource", "resource name"]},
    {"field": "user_email", "label": "Email", "required": False, "aliases": ["email", "user email address", "e-mail"]},
    {"field": "department", "label": "Department / group", "required": False, "aliases": ["dept", "group", "practice"]},
    {"field": "manager_name", "label": "Manager", "required": False, "aliases": ["manager", "line manager"]},
    {"field": "job_code", "label": "Job code (Tech level)", "required": True, "aliases": ["tech level", "level", "job", "grade"]},
    {"field": "project_id", "label": "Project ID", "required": True, "aliases": ["project id", "projectid", "project number"]},
    {"field": "project_name", "label": "Project name", "required": True, "aliases": ["project"]},
    {"field": "customer_name", "label": "Customer / client", "required": False, "aliases": ["customer", "client", "client name"]},
    {"field": "project_owner", "label": "Project owner (PL)", "required": False, "aliases": ["owner", "project manager", "pl"]},
    {"field": "project_stage", "label": "Project stage", "required": False, "aliases": ["stage", "project status", "status"]},
    {"field": "billable", "label": "Billable", "required": False, "aliases": ["is billable", "billable flag"]},
    {"field": "task_name", "label": "Task", "required": False, "aliases": ["task"]},
    {"field": "booking_id", "label": "Booking ID", "required": False, "aliases": ["booking id", "assignment id", "bookingid"]},
    {"field": "booking_type", "label": "Booking type", "required": False, "aliases": ["type", "booking type name"]},
    {"field": "start_date", "label": "Start date", "required": True, "aliases": ["start", "from", "booking start"]},
    {"field": "end_date", "label": "End date", "required": True, "aliases": ["end", "to", "booking end"]},
    {"field": "hours", "label": "Hours (total)", "required": False, "aliases": ["total hours", "booked hours"]},
    {"field": "hours_per_day", "label": "Hours per day", "required": False, "aliases": ["hours/day", "daily hours"]},
    {"field": "role_family", "label": "Role family (optional)", "required": False, "aliases": ["role", "role family"]},
    {"field": "location", "label": "Location (optional)", "required": False, "aliases": ["office", "city"]},
]
FIELD_NAMES = [f["field"] for f in CANONICAL_FIELDS]

STAGE_MAP = {
    "active": "active",
    "in progress": "active",
    "open": "active",
    "pipeline": "pipeline",
    "proposal": "pipeline",
    "pending": "pipeline",
    "on hold": "on_hold",
    "on_hold": "on_hold",
    "hold": "on_hold",
    "closed": "closed",
    "complete": "closed",
    "completed": "closed",
    "lost": "lost",
}


def _norm_col(c: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(c).lower()).strip()


def read_table(content: bytes, file_name: str) -> pd.DataFrame:
    name = (file_name or "").lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        df = pd.read_excel(io.BytesIO(content), dtype=str)
    else:
        text = content.decode("utf-8-sig", errors="replace")
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    df = df.fillna("")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def suggest_mapping(columns: list[str], saved: dict | None = None) -> dict[str, str | None]:
    """Canonical field -> source column. A saved mapping wins wherever its column still exists."""
    saved = saved or {}
    cols = {_norm_col(c): c for c in columns}
    out: dict[str, str | None] = {}
    for f in CANONICAL_FIELDS:
        src = saved.get(f["field"])
        if src and src in columns:
            out[f["field"]] = src
            continue
        cand = [f["field"].replace("_", " ")] + f["aliases"]
        out[f["field"]] = next((cols[_norm_col(a)] for a in cand if _norm_col(a) in cols), None)
    return out


def missing_required(mapping: dict) -> list[str]:
    miss = [f["field"] for f in CANONICAL_FIELDS if f["required"] and not mapping.get(f["field"])]
    if not mapping.get("user_id") and not mapping.get("user_email"):
        miss.append("user_id or user_email")
    if not mapping.get("hours") and not mapping.get("hours_per_day"):
        miss.append("hours or hours_per_day")
    return miss


def parse_date(v) -> date | None:
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    try:
        return pd.to_datetime(s).date()
    except Exception:
        return None


def parse_hours(v) -> float | None:
    if v is None or str(v).strip() == "":
        return None
    try:
        return round(float(str(v).replace(",", "").strip()), 2)
    except ValueError:
        return float("nan")


def parse_bool(v, default: bool = True) -> bool:
    s = str(v or "").strip().lower()
    if s in ("yes", "y", "true", "1", "billable"):
        return True
    if s in ("no", "n", "false", "0", "non-billable", "nonbillable"):
        return False
    return default


def tech_code(v: str) -> str | None:
    """'Tech 5' / 'T5' / '5' / 'Tech5' -> 'T5'."""
    m = re.search(r"(\d+)", str(v or ""))
    return f"T{int(m.group(1))}" if m else None


def clean(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def normalise_row(raw: dict, mapping: dict) -> dict:
    g = lambda f: raw.get(mapping[f], "") if mapping.get(f) else ""  # noqa: E731
    email = clean(g("user_email")).lower() or None
    uid = clean(g("user_id")) or None
    return {
        "user_key": uid or email,
        "user_id": uid,
        "user_name": clean(g("user_name")),
        "user_email": email,
        "department": clean(g("department")) or None,
        "manager_name": clean(g("manager_name")) or None,
        "job_code": clean(g("job_code")),
        "tech_level": tech_code(g("job_code")),
        "project_id": clean(g("project_id")),
        "project_name": clean(g("project_name")),
        "customer_name": clean(g("customer_name")) or None,
        "project_owner": clean(g("project_owner")) or None,
        "project_stage": STAGE_MAP.get(clean(g("project_stage")).lower(), "active") if mapping.get("project_stage") else "active",
        "billable": parse_bool(g("billable")) if mapping.get("billable") else True,
        "task_name": clean(g("task_name")) or None,
        "booking_id": clean(g("booking_id")) or None,
        "booking_type": (clean(g("booking_type")).title() or "Hard") if mapping.get("booking_type") else "Hard",
        "start_date": parse_date(g("start_date")),
        "end_date": parse_date(g("end_date")),
        "hours": parse_hours(g("hours")),
        "hours_per_day": parse_hours(g("hours_per_day")),
        "role_family": clean(g("role_family")) or None,
        "location": clean(g("location")) or None,
    }


def validate(row: dict, known_levels: set[str]) -> list[str]:
    errs = []
    if not row["user_key"]:
        errs.append("missing user_id/user_email")
    if not row["user_name"]:
        errs.append("missing user_name")
    if not row["project_id"]:
        errs.append("missing project_id")
    if not row["project_name"]:
        errs.append("missing project_name")
    if row["start_date"] is None:
        errs.append("start_date does not parse")
    if row["end_date"] is None:
        errs.append("end_date does not parse")
    if row["start_date"] and row["end_date"] and row["end_date"] < row["start_date"]:
        errs.append("end_date before start_date")
    for f in ("hours", "hours_per_day"):
        v = row[f]
        if v is not None and (v != v):
            errs.append(f"{f} is not a number")
        elif v is not None and v < 0:
            errs.append(f"{f} must be >= 0")
    if row["hours"] is None and row["hours_per_day"] is None:
        errs.append("missing hours")
    if not row["tech_level"] or row["tech_level"] not in known_levels:
        errs.append(f"unknown Tech level '{row['job_code']}'")
    return errs


def booking_key(row: dict) -> str:
    if row["booking_id"]:
        return row["booking_id"]
    return f"{row['user_key']}|{row['project_id']}|{row['task_name'] or ''}|{week_start(row['start_date']).isoformat()}"


def row_hash(values: dict) -> str:
    """sha256 of the normalised business columns."""
    payload = json.dumps(values, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


PERSON_COLS = ["user_name", "user_email", "department", "manager_name", "tech_level", "role_family", "location"]
PROJECT_COLS = ["project_name", "customer_name", "project_owner", "project_stage", "billable"]
BOOKING_COLS = ["user_key", "project_id", "task_name", "booking_type", "start_date", "end_date", "hours", "hours_per_day", "billable"]


def pick(row: dict, cols: list[str]) -> dict:
    return {c: (row[c].isoformat() if isinstance(row[c], date) else row[c]) for c in cols}
