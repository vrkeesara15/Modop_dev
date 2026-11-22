"""Business rules from spec section 3, as pure functions (no database access).

Every threshold is passed in from the Admin-editable settings; nothing here is hard-coded
except the defaults used when a setting is missing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

DEFAULT_BANDS = {"bench": 50.0, "light": 80.0, "healthy": 100.0, "hot": 115.0}
DEFAULT_CONFIDENCE_STEPS = [
    {"value": 100, "label": "Signed / active"},
    {"value": 75, "label": "Verbal yes, SOW pending"},
    {"value": 50, "label": "Proposal out"},
    {"value": 25, "label": "Early pipeline"},
    {"value": 0, "label": "Lost / on hold"},
]
STATUS_ORDER = ["bench", "light", "healthy", "hot", "over"]
STATUS_LABELS = {
    "bench": "Bench",
    "light": "Light",
    "healthy": "Healthy",
    "hot": "Hot",
    "over": "Over-allocated",
    "none": "No capacity",
}


# ---------------------------------------------------------------- weeks
def week_start(d: date) -> date:
    """Monday of the week containing d. Weeks start Monday."""
    return d - timedelta(days=d.weekday())


def week_range(start: date, end: date) -> list[date]:
    """All Mondays from week_start(start) to week_start(end), inclusive."""
    w, last, out = week_start(start), week_start(end), []
    while w <= last:
        out.append(w)
        w += timedelta(days=7)
    return out


def working_days_in_week(week: date, start: date, end: date) -> int:
    """Number of Mon-Fri days of `week` that fall inside [start, end]."""
    n = 0
    for i in range(5):
        d = week + timedelta(days=i)
        if start <= d <= end:
            n += 1
    return n


def spread_booking(start: date, end: date, hours_total: float | None, hours_per_day: float | None) -> dict[date, float]:
    """Turn an OpenAir booking (date range + hours) into hours per week.

    hours_per_day wins when present; otherwise total hours are spread evenly across working days.
    """
    if end < start:
        return {}
    weeks = week_range(start, end)
    days = {w: working_days_in_week(w, start, end) for w in weeks}
    total_days = sum(days.values())
    if total_days == 0:
        return {}
    if hours_per_day is not None and hours_per_day > 0:
        return {w: round(hours_per_day * n, 2) for w, n in days.items() if n}
    if not hours_total:
        return {w: 0.0 for w, n in days.items() if n}
    per_day = hours_total / total_days
    return {w: round(per_day * n, 2) for w, n in days.items() if n}


# ---------------------------------------------------------------- capacity & utilization
def capacity_hours(std_hours_week: float, hours_off: float = 0.0) -> float:
    """Capacity = standard hours per week minus holidays and PTO."""
    return max(0.0, float(std_hours_week) - float(hours_off or 0))


def weighted_hours(hours: float, confidence: int) -> float:
    """Weighted hours = requested hours x confidence."""
    return round(float(hours) * max(0, min(100, int(confidence))) / 100.0, 2)


def utilization(confirmed: float, weighted_forecast: float, capacity: float) -> float | None:
    """(Confirmed + weighted forecast) / capacity, as a percentage. None when capacity is 0."""
    if capacity <= 0:
        return None
    return round((confirmed + weighted_forecast) / capacity * 100.0, 1)


def status_band(util_pct: float | None, bands: dict | None = None) -> str:
    b = {**DEFAULT_BANDS, **(bands or {})}
    if util_pct is None:
        return "none"
    if util_pct < b["bench"]:
        return "bench"
    if util_pct < b["light"]:
        return "light"
    if util_pct <= b["healthy"]:
        return "healthy"
    if util_pct <= b["hot"]:
        return "hot"
    return "over"


def is_at_risk(status_confirmed: str, status_forecast: str) -> bool:
    """Healthy (or better) on Forecast but Light/Bench on Confirmed."""
    return status_forecast in ("healthy", "hot", "over") and status_confirmed in ("bench", "light")


@dataclass
class PersonWeek:
    person_id: str
    week_start: date
    capacity: float
    confirmed: float = 0.0
    weighted_forecast: float = 0.0
    raw_forecast: float = 0.0
    billable: float = 0.0
    by_project: dict = field(default_factory=dict)  # project_id -> confirmed + weighted hours
    bands: dict | None = None

    @property
    def util_confirmed(self) -> float | None:
        return utilization(self.confirmed, 0, self.capacity)

    @property
    def util_forecast(self) -> float | None:
        return utilization(self.confirmed, self.weighted_forecast, self.capacity)

    @property
    def util_billable(self) -> float | None:
        return utilization(self.billable, 0, self.capacity)

    @property
    def status_confirmed(self) -> str:
        return status_band(self.util_confirmed, self.bands)

    @property
    def status_forecast(self) -> str:
        return status_band(self.util_forecast, self.bands)

    @property
    def at_risk(self) -> bool:
        return is_at_risk(self.status_confirmed, self.status_forecast)

    @property
    def free_hours(self) -> float:
        return round(max(0.0, self.capacity - self.confirmed - self.weighted_forecast), 2)

    def status(self, mode: str = "forecast") -> str:
        return self.status_confirmed if mode == "confirmed" else self.status_forecast

    def util(self, mode: str = "forecast") -> float | None:
        return self.util_confirmed if mode == "confirmed" else self.util_forecast

    def to_dict(self) -> dict:
        return {
            "person_id": self.person_id,
            "week_start": self.week_start.isoformat(),
            "capacity": round(self.capacity, 2),
            "confirmed_hours": round(self.confirmed, 2),
            "weighted_forecast_hours": round(self.weighted_forecast, 2),
            "raw_forecast_hours": round(self.raw_forecast, 2),
            "billable_hours": round(self.billable, 2),
            "free_hours": self.free_hours,
            "util_confirmed": self.util_confirmed,
            "util_forecast": self.util_forecast,
            "util_billable": self.util_billable,
            "status_confirmed": self.status_confirmed,
            "status_forecast": self.status_forecast,
            "at_risk": self.at_risk,
        }


def effective_confidence(state: str, demand_confidence: int | None, booking_type: str | None, soft_weight: float) -> int:
    """How much of an allocation's hours count, 0-100.

    - released / declined allocations count for nothing.
    - an allocation linked to a demand line uses that line's confidence.
    - an OpenAir "Soft" booking without a demand line counts at the soft-booking weight.
    - proposed / requested allocations without a demand line count at the soft weight too.
    - anything else (confirmed) counts in full.
    """
    if state in ("released", "declined"):
        return 0
    if demand_confidence is not None:
        return int(demand_confidence)
    if (booking_type or "").lower() == "soft" or state in ("proposed", "requested"):
        return int(round(soft_weight * 100))
    return 100


def add_allocation_hours(pw: PersonWeek, project_id: str, hours: float, confidence: int, billable: bool, state: str) -> None:
    """Add one allocation-week to a person-week: 100% confirmed counts as firm work."""
    if confidence <= 0 or hours <= 0:
        return
    if confidence >= 100 and state == "confirmed":
        pw.confirmed += hours
        if billable:
            pw.billable += hours
        pw.by_project[project_id] = pw.by_project.get(project_id, 0.0) + hours
    else:
        w = weighted_hours(hours, confidence)
        pw.weighted_forecast += w
        pw.raw_forecast += hours
        pw.by_project[project_id] = pw.by_project.get(project_id, 0.0) + w


def is_billable(project_status: str, project_billable: bool, project_start: date | None, project_end: date | None, week: date) -> bool:
    """Billable when the project is active, flagged billable, and the week is inside the project's dates."""
    if project_status != "active" or not project_billable:
        return False
    week_end = week + timedelta(days=6)
    if project_start and week_end < project_start:
        return False
    if project_end and week > project_end:
        return False
    return True


# ---------------------------------------------------------------- hot team driver
def project_hours(person_weeks: list[PersonWeek]) -> dict[str, float]:
    out: dict[str, float] = {}
    for pw in person_weeks:
        for pid, h in pw.by_project.items():
            out[pid] = out.get(pid, 0.0) + h
    return out


def baseline_hours(weekly: list[dict[str, float]]) -> dict[str, float]:
    """Typical (median) weekly hours per project across a window of weeks for one group."""
    pids = {pid for w in weekly for pid in w}
    out = {}
    for pid in pids:
        vals = sorted(w.get(pid, 0.0) for w in weekly)
        n = len(vals)
        out[pid] = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    return out


def hot_team_driver(person_weeks: list[PersonWeek], spike_threshold: float = 0.6, baseline: dict[str, float] | None = None) -> dict:
    """Explain what drives a Hot group in one week: project spike vs structural gap.

    overload = group load (confirmed + weighted forecast) minus group capacity.
    With a baseline (each project's typical weekly hours for this group), a project's contribution is
    its hours above its baseline; without one it is its share of the group's hours. If the top
    project accounts for more than `spike_threshold` (default 60%) of the overload, the week is a
    "project spike"; otherwise it is a "structural gap" (a hiring signal).
    """
    cap = sum(p.capacity for p in person_weeks)
    load = sum(p.confirmed + p.weighted_forecast for p in person_weeks)
    overload = load - cap
    hours = project_hours(person_weeks)
    if overload <= 0 or not hours:
        return {"driver": None, "top_project_id": None, "top_project_share": 0.0, "overload_hours": round(max(0.0, overload), 2)}
    if baseline is not None:
        contrib = {pid: h - baseline.get(pid, 0.0) for pid, h in hours.items()}
        top_pid, top_h = max(contrib.items(), key=lambda kv: kv[1])
        share = max(0.0, min(1.0, top_h / overload))
    else:
        top_pid, top_h = max(hours.items(), key=lambda kv: kv[1])
        share = top_h / load if load else 0.0
    return {
        "driver": "project_spike" if share > spike_threshold else "structural_gap",
        "top_project_id": top_pid,
        "top_project_share": round(share, 3),
        "top_project_hours": round(hours.get(top_pid, 0.0), 2),
        "overload_hours": round(overload, 2),
    }


def group_rollup(person_weeks: list[PersonWeek], bands: dict | None = None) -> dict:
    cap = sum(p.capacity for p in person_weeks)
    conf = sum(p.confirmed for p in person_weeks)
    wf = sum(p.weighted_forecast for p in person_weeks)
    bill = sum(p.billable for p in person_weeks)
    uc, uf = utilization(conf, 0, cap), utilization(conf, wf, cap)
    return {
        "capacity": round(cap, 2),
        "confirmed_hours": round(conf, 2),
        "weighted_forecast_hours": round(wf, 2),
        "billable_hours": round(bill, 2),
        "util_confirmed": uc,
        "util_forecast": uf,
        "util_billable": utilization(bill, 0, cap),
        "status_confirmed": status_band(uc, bands),
        "status_forecast": status_band(uf, bands),
        "headcount": len(person_weeks),
    }


# ---------------------------------------------------------------- matching (AVL-3)
def match_score(
    *,
    person_rank: int | None,
    min_rank: int | None,
    person_skills: dict[str, int],
    wanted_skills: list[str],
    free_hours: float,
    wanted_hours: float,
    continuity: bool,
    role_match: bool,
) -> float:
    """Rank candidates by level fit, skill match, free hours and current-project continuity (0-100)."""
    # Level fit: exact level is best; one level above is fine; below is not eligible.
    if min_rank is None or person_rank is None:
        level = 0.5
    elif person_rank < min_rank:
        return 0.0
    else:
        level = max(0.0, 1.0 - 0.35 * (person_rank - min_rank))
    if wanted_skills:
        hits = [person_skills.get(s.lower(), 0) for s in wanted_skills]
        skill = sum(min(h, 3) / 3 for h in hits) / len(wanted_skills)
    else:
        skill = 1.0
    hours = min(1.0, free_hours / wanted_hours) if wanted_hours > 0 else 1.0
    score = 35 * level + 30 * skill + 25 * hours + 10 * (1.0 if continuity else 0.0)
    if not role_match:
        score *= 0.6
    return round(score, 1)


def margin_warning(requested_rate: float | None, person_rate: float | None) -> float | None:
    """Rate difference when a higher level than requested is staffed (positive = cost above plan)."""
    if requested_rate is None or person_rate is None:
        return None
    diff = round(float(person_rate) - float(requested_rate), 2)
    return diff if diff > 0 else None
