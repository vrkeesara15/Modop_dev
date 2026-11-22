from datetime import date

from app.services import rules
from app.services.rules import PersonWeek


def test_week_start_is_monday():
    assert rules.week_start(date(2026, 11, 22)) == date(2026, 11, 16)  # Sunday -> Monday before
    assert rules.week_start(date(2026, 11, 23)) == date(2026, 11, 23)


def test_capacity_minus_pto():
    assert rules.capacity_hours(40, 16) == 24
    assert rules.capacity_hours(40, 60) == 0


def test_weighted_hours_and_confidence_steps():
    assert rules.weighted_hours(40, 75) == 30
    assert rules.weighted_hours(40, 50) == 20
    assert rules.weighted_hours(40, 25) == 10
    assert rules.weighted_hours(40, 0) == 0


def test_utilization_and_bands():
    assert rules.utilization(30, 10, 40) == 100.0
    assert rules.utilization(10, 0, 0) is None
    assert rules.status_band(49.9) == "bench"
    assert rules.status_band(50) == "light"
    assert rules.status_band(79.9) == "light"
    assert rules.status_band(80) == "healthy"
    assert rules.status_band(100) == "healthy"
    assert rules.status_band(100.1) == "hot"
    assert rules.status_band(115) == "hot"
    assert rules.status_band(115.1) == "over"
    # bands are admin-editable
    assert rules.status_band(85, {"bench": 40, "light": 90, "healthy": 100, "hot": 110}) == "light"


def test_confirmed_vs_forecast_and_at_risk():
    pw = PersonWeek(person_id="p", week_start=date(2026, 11, 23), capacity=40)
    rules.add_allocation_hours(pw, "a", 16, 100, True, "confirmed")
    rules.add_allocation_hours(pw, "b", 24, 75, True, "proposed")
    assert pw.confirmed == 16 and pw.weighted_forecast == 18 and pw.raw_forecast == 24
    assert pw.util_confirmed == 40.0 and pw.util_forecast == 85.0
    assert pw.status_confirmed == "bench" and pw.status_forecast == "healthy"
    assert pw.at_risk
    assert pw.billable == 16  # only firm billable work counts toward billable utilization


def test_zero_confidence_excluded():
    pw = PersonWeek(person_id="p", week_start=date(2026, 11, 23), capacity=40)
    rules.add_allocation_hours(pw, "a", 40, 0, True, "proposed")
    assert pw.util_forecast == 0


def test_effective_confidence():
    assert rules.effective_confidence("released", 100, None, 0.5) == 0
    assert rules.effective_confidence("confirmed", 75, None, 0.5) == 75
    assert rules.effective_confidence("confirmed", None, "Soft", 0.5) == 50
    assert rules.effective_confidence("confirmed", None, "Hard", 0.5) == 100


def test_billable_rule():
    w = date(2026, 12, 7)
    assert rules.is_billable("active", True, date(2026, 12, 1), date(2027, 1, 1), w)
    assert not rules.is_billable("pipeline", True, None, None, w)
    assert not rules.is_billable("active", False, None, None, w)
    assert not rules.is_billable("active", True, date(2027, 1, 1), None, w)


def test_spread_booking_by_hours_per_day_and_total():
    # Tue Dec 1 .. Thu Dec 31 2026, 6 h/day
    weeks = rules.spread_booking(date(2026, 12, 1), date(2026, 12, 31), 120, 6)
    assert weeks[date(2026, 11, 30)] == 24  # Tue-Fri
    assert weeks[date(2026, 12, 28)] == 24  # Mon-Thu
    even = rules.spread_booking(date(2026, 11, 30), date(2026, 12, 11), 80, None)
    assert even == {date(2026, 11, 30): 40, date(2026, 12, 7): 40}


def _pw(cap, hours_by_project):
    pw = PersonWeek(person_id="x", week_start=date(2026, 12, 7), capacity=cap)
    for pid, h in hours_by_project.items():
        rules.add_allocation_hours(pw, pid, h, 100, True, "confirmed")
    return pw


def test_hot_team_driver_project_spike_vs_structural():
    # baseline: each person 36h on "base"; spike week adds 18h on "surge" for 3 of 4 people
    normal = [_pw(40, {"base": 36}) for _ in range(4)]
    spike = [_pw(40, {"base": 36, "surge": 18}) for _ in range(3)] + [_pw(40, {"base": 36})]
    base = rules.baseline_hours([rules.project_hours(normal)] * 3 + [rules.project_hours(spike)])
    d = rules.hot_team_driver(spike, 0.6, base)
    assert d["driver"] == "project_spike" and d["top_project_id"] == "surge"
    # structural: always 44h spread over several projects, no change vs baseline
    flat = [_pw(40, {"p1": 22, "p2": 22}), _pw(40, {"p3": 22, "p4": 22})]
    base2 = rules.baseline_hours([rules.project_hours(flat)] * 4)
    assert rules.hot_team_driver(flat, 0.6, base2)["driver"] == "structural_gap"
    # not hot -> no driver
    assert rules.hot_team_driver(normal, 0.6, base)["driver"] is None


def test_match_score_prefers_level_fit_and_skills():
    kw = dict(person_skills={"sql": 3}, wanted_skills=["SQL"], free_hours=20, wanted_hours=20, continuity=False, role_match=True)
    exact = rules.match_score(person_rank=5, min_rank=5, **kw)
    higher = rules.match_score(person_rank=7, min_rank=5, **kw)
    lower = rules.match_score(person_rank=4, min_rank=5, **kw)
    assert exact > higher > 0 and lower == 0
    assert rules.margin_warning(150, 185) == 35
    assert rules.margin_warning(150, 150) is None
