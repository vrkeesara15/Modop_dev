"""Demo seed and sample OpenAir exports.

    python -m app.seed --demo              # seed an empty database (idempotent: skips if users exist)
    python -m app.seed --demo --reset      # wipe and re-seed
    python -m app.seed --write-samples ../sample_data [--anchor 2026-11-23]

The demo data is synthetic: 60 people in 5 groups, Tech 3-7, 20 projects (15 active from OpenAir,
5 pipeline created in the app at 25-75%), 12 weeks of allocations from the current Monday (plus 4
weeks of history). Group B is deliberately Hot in weeks 3-6 from one project (a project spike),
Group C is Hot every week across many projects (a structural gap), and 6 people are Light.
OpenAir data is loaded by running the real import pipeline on the generated day-1 export, so the
seeded state is exactly what an upload of that file produces; day-2 differs by 14 changed, 2 new
and 1 removed rows.
"""
from __future__ import annotations

import argparse
import csv
import io
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select, text

from app.config import get_settings
from app.db import SessionLocal, engine
from app.services.rules import week_start

EXPORT_COLUMNS = [
    "user_id", "user_name", "user_email", "department", "manager_name", "job_code", "project_id", "project_name", "customer_name",
    "project_owner", "project_stage", "billable", "task_name", "booking_id", "booking_type", "start_date", "end_date", "hours", "hours_per_day",
]

USERS = [
    ("admin@demo", "Vamshi Admin", ["admin"]),
    ("sl@demo", "Sam Leader", ["sl"]),
    ("gl@demo", "Gina Morales", ["gl"]),
    ("pl@demo", "Jordan Price", ["pl"]),
    ("pl2@demo", "Taylor Reed", ["pl"]),
    ("pl3@demo", "Morgan Diaz", ["pl"]),
    ("gl.a@demo", "Avery Chen", ["gl"]),
    ("gl.c@demo", "Chris Okafor", ["gl"]),
    ("gl.d@demo", "Dana Kim", ["gl"]),
    ("gl.e@demo", "Evan Brooks", ["gl"]),
]

GROUPS = [  # key, name, role family, GL email, size
    ("A", "Group A - Analytics", "Analyst", "gl.a@demo", 12),
    ("B", "Group B - Data Engineering", "Data Engineer", "gl@demo", 16),
    ("C", "Group C - Development", "Developer", "gl.c@demo", 12),
    ("D", "Group D - Design", "Designer", "gl.d@demo", 10),
    ("E", "Group E - Delivery", "PM", "gl.e@demo", 10),
]

LEVELS = [("T3", 3, "Tech 3 - Associate"), ("T4", 4, "Tech 4 - Consultant"), ("T5", 5, "Tech 5 - Senior Consultant"), ("T6", 6, "Tech 6 - Lead"), ("T7", 7, "Tech 7 - Principal")]
RATES = {"T3": 95, "T4": 120, "T5": 150, "T6": 185, "T7": 225}

SKILLS = {
    "Analyst": ["SQL", "Tableau", "Power BI", "Python", "Excel"],
    "Data Engineer": ["SQL", "Python", "BigQuery", "Snowflake", "dbt", "Spark", "Airflow", "AWS"],
    "Developer": ["Java", "React", "Python", "AWS", "Azure", "SQL", "Salesforce"],
    "Designer": ["Figma", "UX Research", "Prototyping", "Accessibility"],
    "PM": ["Agile/Scrum", "Salesforce", "Budgeting", "Stakeholder Management"],
}
SKILL_CATEGORY = {"SQL": "Data", "Python": "Data", "BigQuery": "Cloud data", "Snowflake": "Cloud data", "dbt": "Data", "Spark": "Data", "Airflow": "Data", "AWS": "Cloud", "Azure": "Cloud", "Tableau": "BI", "Power BI": "BI", "Excel": "BI", "Java": "Engineering", "React": "Engineering", "Salesforce": "CRM", "Figma": "Design", "UX Research": "Design", "Prototyping": "Design", "Accessibility": "Design", "Agile/Scrum": "Delivery", "Budgeting": "Delivery", "Stakeholder Management": "Delivery"}

FIRST = ["Priya", "Liam", "Noah", "Emma", "Olivia", "Ava", "Mateo", "Sofia", "Arjun", "Mei", "Lucas", "Isabella", "Ethan", "Mia", "Aiden", "Zara", "Omar", "Leah", "Ravi", "Nina", "Kenji", "Elena", "Diego", "Hana",
         "Samuel", "Grace", "Tariq", "Chloe", "Vikram", "Ana", "Jonah", "Lena", "Felix", "Maya", "Hugo", "Iris", "Kofi", "Rosa", "Andre", "Yara", "Milan", "Sara", "Theo", "Lucia", "Nikhil", "Freya", "Marcus", "Aisha",
         "Owen", "Julia", "Rohan", "Clara", "Dev", "Nora", "Ivan", "Leila", "Caleb", "Anika", "Pablo", "Ines"]
LAST = ["Sharma", "Walker", "Bennett", "Garcia", "Nguyen", "Okafor", "Silva", "Rossi", "Iyer", "Tanaka", "Muller", "Haddad", "Kowalski", "Novak", "Fischer", "Costa", "Reyes", "Hughes", "Menon", "Larsen", "Sato",
        "Petrov", "Alvarez", "Park", "Mensah", "Dubois", "Rahman", "Moreau", "Rao", "Santos", "Klein", "Lopez", "Berg", "Ito", "Laurent", "Jensen", "Owusu", "Romero", "Wright", "Nasser", "Horvat", "Lindqvist", "Byrne",
        "Ferreira", "Kapoor", "Olsen", "Grant", "Hassan", "Price", "Weber", "Desai", "Martin", "Joshi", "Ward", "Popov", "Karimi", "Brooks", "Mehta", "Vega", "Duarte"]
LOCATIONS = ["Chicago", "Dallas", "Remote", "Toronto", "Hyderabad"]

PROJECTS = [  # openair id, name, client, PL name, billable
    ("P-2291", "Retail Data Platform", "Acme Retail", "Jordan Price", True),
    ("P-2301", "Customer 360", "Contoso", "Taylor Reed", True),
    ("P-2310", "Claims Modernization", "Humongous Insurance", "Morgan Diaz", True),
    ("P-2315", "Payments API", "Woodgrove Bank", "Jordan Price", True),
    ("P-2320", "Supply Chain Analytics", "Wide World Importers", "Taylor Reed", True),
    ("P-2333", "Portal Redesign", "Alpine Ski House", "Morgan Diaz", True),
    ("P-2340", "Finance Dashboards", "Fourth Coffee", "Jordan Price", True),
    ("P-2345", "Data Lakehouse", "Proseware", "Taylor Reed", True),
    ("P-2350", "Mobile Ordering", "Coho Winery", "Morgan Diaz", True),
    ("P-2360", "Ops Automation", "Trey Research", "Jordan Price", True),
    ("P-2370", "Booking Tracker", "Margie's Travel", "Taylor Reed", True),
    ("P-2380", "Reader App UI", "Lucerne Publishing", "Morgan Diaz", True),
    ("P-2390", "Bench Training", "Internal", "Morgan Diaz", False),
    ("P-2395", "Pre-sales Support", "Internal", "Taylor Reed", False),
    ("P-2398", "PMO Services", "Wingtip Toys", "Jordan Price", True),
]
PROJECT_BY_ID = {p[0]: p for p in PROJECTS}

FIRST_WEEK, LAST_WEEK = -4, 11  # week indexes relative to the anchor Monday


@dataclass
class Booking:
    booking_id: str
    project_id: str
    task: str
    w_from: int
    w_to: int
    hours_week: float
    booking_type: str = "Hard"


@dataclass
class PersonSpec:
    key: str
    user_id: str
    name: str
    group: str
    level: str
    location: str
    skills: dict
    bookings: list[Booking] = field(default_factory=list)

    @property
    def email(self) -> str:
        return self.name.lower().replace(" ", ".") + "@example.com"


def build_people() -> list[PersonSpec]:
    rng = random.Random(20261120)
    people: list[PersonSpec] = []
    n = 0
    bid = [88000]

    def booking(project, task, wf, wt, h, btype="Hard"):
        bid[0] += 7
        return Booking(f"B-{bid[0]}", project, task, wf, wt, h, btype)

    for gkey, gname, role, _gl, size in GROUPS:
        for i in range(size):
            idx = {0: 12, 12: 0}.get(n, n)  # B0 is Priya Sharma (user 1042), as in the spec's sample row
            name = f"{FIRST[idx]} {LAST[idx]}"
            uid = str(1042 if n == 12 else 1100 + n * 3)
            lvl = rng.choices(["T3", "T4", "T5", "T6", "T7"], weights=[10, 30, 35, 20, 5])[0]
            pool = SKILLS[role]
            sk = {s: rng.randint(1, 3) for s in rng.sample(pool, k=min(len(pool), rng.randint(2, 4)))}
            p = PersonSpec(f"{gkey}{i}", uid, name, gname, lvl, LOCATIONS[(n * 7 + i) % len(LOCATIONS)], sk)
            n += 1
            F, L = FIRST_WEEK, LAST_WEEK
            if gkey == "A":
                if i < 2:  # light
                    p.bookings.append(booking("P-2340", "Dashboard build", F, L, 24))
                elif i < 5:
                    p.bookings += [booking("P-2320", "Demand forecasting", F, L, 32), booking("P-2395", "Proposal analytics", F, L, 4)]
                elif i < 9:
                    p.bookings.append(booking("P-2320" if i % 2 else "P-2340", "Reporting", F, L, 36))
                else:
                    p.bookings += [booking("P-2340", "KPI model", F, L, 20), booking("P-2301", "Customer analytics", F, L, 16)]
            elif gkey == "B":
                if i == 0:
                    p.level, p.skills = "T5", {"SQL": 3, "BigQuery": 2, "Python": 2}
                if i < 9:  # Group B surge on one project in weeks 3-6
                    p.bookings.append(booking("P-2291" if i % 3 else "P-2345", "Pipeline build", F, L, 36))
                    p.bookings.append(booking("P-2291", "Pipeline build surge", 2, 5, 18))
                elif i < 12:  # make room for the Northwind pipeline project in weeks 3-6
                    p.level = ["T4", "T5", "T5"][i - 9]
                    p.bookings += [booking("P-2345", "Lakehouse ingestion", F, 1, 34), booking("P-2345", "Lakehouse ingestion (part-time)", 2, 5, 20), booking("P-2345", "Lakehouse ingestion", 6, L, 34)]
                elif i < 14:  # light Tech 5 + SQL people
                    p.level, p.skills = "T5", {"SQL": 3, "Python": 2, "Snowflake" if i == 12 else "dbt": 2}
                    p.bookings.append(booking("P-2345", "Data quality", F, L, 20))
                else:
                    p.bookings.append(booking("P-2291", "Platform support", F, L, 36))
            elif gkey == "C":  # structural: everyone at 44 h across many projects
                a, b = [("P-2310", "P-2315"), ("P-2350", "P-2360"), ("P-2370", "P-2310"), ("P-2315", "P-2360")][i % 4]
                if i % 3 == 0:
                    p.bookings.append(booking(a, "Feature development", F, L, 44))
                else:
                    p.bookings += [booking(a, "Feature development", F, L, 28), booking(b, "API work", F, L, 16)]
            elif gkey == "D":
                if i < 2:  # light
                    p.bookings += [booking("P-2380", "UI design", F, L, 20), booking("P-2390", "Design system training", F, L, 8)]
                elif i == 2:  # rolls off, has next work
                    p.bookings += [booking("P-2333", "Portal UX", F, 1, 36), booking("P-2350", "Ordering UX", 2, L, 36)]
                elif i == 3:  # rolls off with no next work
                    p.bookings.append(booking("P-2333", "Portal UX", F, 1, 38))
                else:
                    p.bookings.append(booking(["P-2333", "P-2350", "P-2380"][i % 3], "Product design", F, L, 36 + (i % 3) * 2))
            else:  # E delivery
                p.bookings.append(booking(["P-2398", "P-2360", "P-2310", "P-2291"][i % 4], "Project management", F, L, 30))
                p.bookings.append(booking("P-2398" if i % 4 else "P-2395", "Governance", F, L, 8))
                if i in (2, 5):
                    p.bookings.append(booking("P-2398", "Phase 2 extension", 8, L, 8, "Soft"))
            people.append(p)
    return people


def _row(p: PersonSpec, b: Booking, anchor: date) -> dict:
    prj = PROJECT_BY_ID[b.project_id]
    start = anchor + timedelta(weeks=b.w_from)
    end = anchor + timedelta(weeks=b.w_to, days=4)
    days = sum(1 for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5)
    hpd = round(b.hours_week / 5, 2)
    gl = next(g for g in GROUPS if g[1] == p.group)[3]
    gl_name = next(u[1] for u in USERS if u[0] == gl)
    return {
        "user_id": p.user_id, "user_name": p.name, "user_email": p.email, "department": p.group, "manager_name": gl_name,
        "job_code": f"Tech {p.level[1:]}", "project_id": prj[0], "project_name": prj[1], "customer_name": prj[2], "project_owner": prj[3],
        "project_stage": "Active", "billable": "Yes" if prj[4] else "No", "task_name": b.task, "booking_id": b.booking_id, "booking_type": b.booking_type,
        "start_date": start.isoformat(), "end_date": end.isoformat(), "hours": f"{hpd * days:g}", "hours_per_day": f"{hpd:g}",
    }


def build_rows(anchor: date, day: int = 1) -> list[dict]:
    people = build_people()
    rows = [_row(p, b, anchor) for p in people for b in p.bookings]
    if day == 2:
        by_id = {r["booking_id"]: r for r in rows}
        protected = {b.booking_id for p in people if p.key.startswith("B") or p.key in ("A0", "A1", "D0", "D1", "D2", "D3") for b in p.bookings}
        eligible = sorted(bid for bid in by_id if bid not in protected and by_id[bid]["booking_type"] == "Hard")
        removed = next(b.booking_id for p in people if p.key == "E9" for b in p.bookings if b.task == "Governance")
        eligible = [e for e in eligible if e != removed]
        changed = eligible[1::4][:14]
        assert len(changed) == 14, len(changed)
        for k, bid in enumerate(changed):
            r = by_id[bid]
            if k % 3 == 2:  # extend by two weeks
                r["end_date"] = (date.fromisoformat(r["end_date"]) + timedelta(weeks=2)).isoformat()
            else:  # +/- 4 h per week
                hpd = float(r["hours_per_day"]) + (0.8 if k % 2 == 0 else -0.8)
                r["hours_per_day"] = f"{round(hpd, 2):g}"
            s, e = date.fromisoformat(r["start_date"]), date.fromisoformat(r["end_date"])
            days = sum(1 for i in range((e - s).days + 1) if (s + timedelta(days=i)).weekday() < 5)
            r["hours"] = f"{round(float(r['hours_per_day']) * days, 2):g}"
        rows = [r for r in rows if r["booking_id"] != removed]
        newp = PersonSpec("A12", "1301", "Riley Nguyen", "Group A - Analytics", "T4", "Chicago", {"SQL": 2, "Tableau": 2})
        rows.append(_row(newp, Booking("B-89901", "P-2320", "Demand forecasting", 1, LAST_WEEK, 24), anchor))
        d0 = next(p for p in people if p.key == "D0")
        rows.append(_row(d0, Booking("B-89902", "P-2395", "Pitch design support", 0, 5, 8), anchor))
    rows.sort(key=lambda r: r["booking_id"])
    return rows


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def sample_csv(day: int = 1, anchor: date | None = None) -> str:
    return to_csv(build_rows(anchor or week_start(date.today()), day))


# ---------------------------------------------------------------- database seed
def reset_db() -> None:
    with engine.begin() as c:
        tables = [r[0] for r in c.execute(text("select tablename from pg_tables where schemaname='public' and tablename <> 'alembic_version'"))]
        if tables:
            c.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE"))


def seed_demo(anchor: date | None = None, verbose: bool = True) -> bool:
    from app.auth.local import hash_password
    from app.ingestion import pipeline
    from app.models import (
        Allocation, AllocationWeek, AppSetting, AvailabilityFlag, CapacityException, ColumnMapping, DemandLine, Group, Person, PersonSkill,
        Project, RateCard, Skill, TechLevel, User, UserRole,
    )
    from app.services import alerts as alerts_svc
    from app.services import audit
    from app.services.rules import week_range

    anchor = anchor or week_start(date.today())
    s = get_settings()
    db = SessionLocal()
    try:
        if db.scalar(select(func.count()).select_from(User)):
            if verbose:
                print("database already has users; skipping demo seed")
            return False
        actor = "seed"
        levels = {}
        for code, rank, desc in LEVELS:
            t = TechLevel(code=code, rank=rank, description=desc, updated_by=actor)
            db.add(t)
            levels[code] = t
        db.flush()
        eff = date(anchor.year - 1, 1, 1)
        for code, rate in RATES.items():
            db.add(RateCard(tech_level_id=levels[code].id, rate_usd_hr=rate, effective_from=eff, updated_by=actor))
        db.add(RateCard(tech_level_id=levels["T5"].id, role_family="Data Engineer", rate_usd_hr=160, effective_from=eff, updated_by=actor))
        db.add(RateCard(tech_level_id=levels["T6"].id, role_family="Data Engineer", rate_usd_hr=195, effective_from=eff, updated_by=actor))
        skills = {}
        for name, cat in SKILL_CATEGORY.items():
            sk = Skill(name=name, category=cat, updated_by=actor)
            db.add(sk)
            skills[name] = sk
        users = {}
        pw = hash_password(s.DEMO_PASSWORD)
        for email, name, roles in USERS:
            u = User(email=email, name=name, password_hash=pw, updated_by=actor)
            u.roles = [UserRole(role=r, updated_by=actor) for r in roles]
            db.add(u)
            users[email] = u
        db.flush()
        groups = {}
        for key, name, _role, gl, _n in GROUPS:
            g = Group(name=name, gl_user_id=users[gl].id, sl_user_id=users["sl@demo"].id, updated_by=actor)
            db.add(g)
            groups[key] = g
        db.flush()
        for key, g in groups.items():
            u = users[next(x[3] for x in GROUPS if x[0] == key)]
            for r in u.roles:
                if r.role == "gl":
                    r.scope_group_id = g.id
        db.add(AppSetting(key="locations", value=LOCATIONS, updated_by=actor))
        db.add(AppSetting(key="demo_anchor", value=anchor.isoformat(), updated_by=actor))
        db.commit()

        # People are created from the OpenAir export; role family, location and skills are app data.
        content = sample_csv(1, anchor).encode()
        mapping = {f: f for f in EXPORT_COLUMNS}
        db.add(ColumnMapping(report_type=pipeline.DEFAULT_REPORT, mapping=mapping, updated_by=actor))
        db.commit()
        rf = pipeline.store_raw(db, content, "openair_export_day1.csv", "upload", actor)
        db.commit()
        run_id = pipeline.run_import(rf.id, mapping=mapping, actor="admin@demo")
        db.expire_all()
        from app.models import ImportRun

        run = db.get(ImportRun, run_id)
        if run.status != "succeeded":
            raise RuntimeError(f"seed import failed: {run.error}")
        specs = {p.user_id: p for p in build_people()}
        role_of = {g[1]: g[2] for g in GROUPS}
        people = {p.openair_user_id: p for p in db.scalars(select(Person))}
        by_key = {}
        for uid, spec in specs.items():
            p = people[uid]
            p.role_family = role_of[spec.group]
            p.location = spec.location
            for sname, prof in spec.skills.items():
                p.skills.append(PersonSkill(person_id=p.id, skill_id=skills[sname].id, proficiency=prof, updated_by=actor))
            by_key[spec.key] = p
        db.flush()

        W = lambda i: anchor + timedelta(weeks=i)  # noqa: E731
        u = users
        # pipeline projects created in the app (DEM-5), 25-75% confidence
        def project(name, client, pl, conf_note, w_from, w_to, status="pipeline", billable=True):
            prj = Project(name=name, client=client, pl_user_id=u[pl].id, status=status, billable=billable, start_date=W(w_from), end_date=W(w_to) + timedelta(days=4), source="app", updated_by=pl)
            db.add(prj)
            db.flush()
            audit.log(db, entity="project", entity_id=prj.id, field=None, old=None, new=f"{name} ({conf_note})", actor=pl, action="insert")
            return prj

        def line(prj, role, lvl, sk, hours, w_from, w_to, conf, person=None, notes=None, by="pl@demo"):
            dl = DemandLine(project_id=prj.id, role_family=role, min_tech_level_id=levels[lvl].id, skills=sk, hours_per_week=hours, start_week=W(w_from), end_week=W(w_to), confidence=conf, requested_person_id=person.id if person else None, notes=notes, updated_by=by)
            db.add(dl)
            db.flush()
            audit.log(db, entity="demand_line", entity_id=dl.id, field=None, old=None, new=f"{prj.name}: {role} {lvl} {hours:g} h/wk @ {conf}%", actor=by, action="insert")
            return dl

        nw = project("Holiday Demand Analytics", "Northwind Traders", "pl@demo", "75%", 2, 5)
        for k in ("B9", "B10", "B11"):
            per = by_key[k]
            dl = line(nw, "Data Engineer", "T4", ["SQL", "Python"], 20, 2, 5, 75, per, "Seasonal demand model; SOW pending", "pl@demo")
            a = Allocation(person_id=per.id, project_id=nw.id, demand_line_id=dl.id, state="proposed", source="app", billable=True, start_date=W(2), end_date=W(5) + timedelta(days=4), edited_in_app=True, updated_by="pl@demo")
            db.add(a)
            db.flush()
            for w in week_range(W(2), W(5)):
                db.add(AllocationWeek(allocation_id=a.id, week_start=w, hours=20, edited_in_app=True, pending_openair=False, updated_by="pl@demo"))
        fab = project("Cloud Migration", "Fabrikam", "pl2@demo", "50%", 4, 11)
        line(fab, "Developer", "T5", ["AWS", "Java"], 30, 4, 11, 50, notes="Proposal out", by="pl2@demo")
        line(fab, "Developer", "T4", ["React"], 20, 6, 11, 50, by="pl2@demo")
        tsp = project("Mobile App", "Tailspin Toys", "pl@demo", "25%", 6, 11)
        line(tsp, "Developer", "T4", ["React"], 40, 6, 11, 25, notes="Early pipeline")
        line(tsp, "Designer", "T4", ["Figma"], 20, 6, 9, 25)
        lit = project("Data Governance", "Litware", "pl3@demo", "50%", 3, 10)
        line(lit, "Analyst", "T5", ["SQL", "Power BI"], 20, 3, 10, 50, person=by_key["A0"], notes="Soft request for a known analyst", by="pl3@demo")
        adv = project("BI Refresh", "Adventure Works", "pl2@demo", "75%", 2, 9)
        line(adv, "Analyst", "T4", ["Tableau"], 16, 2, 9, 75, person=by_key["A1"], by="pl2@demo")
        line(adv, "Data Engineer", "T6", ["Snowflake", "dbt"], 24, 3, 9, 75, by="pl2@demo")
        # open, signed demand on an active OpenAir project (demo step 5)
        c360 = db.scalar(select(Project).where(Project.openair_project_id == "P-2301"))
        line(c360, "Data Engineer", "T5", ["SQL"], 20, 1, 9, 100, notes="Extra pipeline capacity for phase 2", by="pl2@demo")

        # manual availability flags (PPL-3) and PTO (PPL-5)
        db.add(AvailabilityFlag(person_id=by_key["D3"].id, from_date=W(2), hours_per_week=40, note="Rolling off Portal Redesign", set_by="gl.d@demo", updated_by="gl.d@demo"))
        db.add(AvailabilityFlag(person_id=by_key["B12"].id, from_date=W(0), hours_per_week=20, note="Wants more data engineering work", set_by="gl@demo", updated_by="gl@demo"))
        for k, wk in (("A5", 1), ("C4", 3), ("E2", 5)):
            for d in range(2):
                db.add(CapacityException(person_id=by_key[k].id, date=W(wk) + timedelta(days=d), hours_off=8, reason="PTO", updated_by=actor))
        db.flush()
        alerts_svc.recompute(db, None, actor="seed")
        db.commit()
        if verbose:
            print(f"demo seed done: {len(specs)} people, {len(PROJECTS) + 5} projects, anchor {anchor}, import run {run_id}")
        return True
    finally:
        db.close()


def write_samples(out_dir: str, anchor: date) -> None:
    import os

    os.makedirs(out_dir, exist_ok=True)
    for day in (1, 2):
        with open(os.path.join(out_dir, f"openair_export_day{day}.csv"), "w", newline="") as f:
            f.write(to_csv(build_rows(anchor, day)))
    print(f"wrote samples to {out_dir} anchored at {anchor}")


def bootstrap_admin() -> None:
    """Create the first Admin from ADMIN_EMAIL / ADMIN_PASSWORD when the database has no users (non-demo deploys)."""
    from app.auth.local import hash_password
    from app.models import User, UserRole

    s = get_settings()
    if not (s.ADMIN_EMAIL and s.ADMIN_PASSWORD):
        return
    db = SessionLocal()
    try:
        if db.scalar(select(func.count()).select_from(User)):
            return
        u = User(email=s.ADMIN_EMAIL.lower(), name="Administrator", password_hash=hash_password(s.ADMIN_PASSWORD), updated_by="bootstrap")
        u.roles = [UserRole(role="admin", updated_by="bootstrap")]
        db.add(u)
        db.commit()
        print(f"created admin {u.email}")
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--bootstrap-admin", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--write-samples", metavar="DIR")
    ap.add_argument("--anchor", type=date.fromisoformat, default=None, help="Monday the 12 weeks start from (default: current Monday)")
    a = ap.parse_args()
    if a.write_samples:
        write_samples(a.write_samples, week_start(a.anchor or date.today()))
    if a.bootstrap_admin:
        bootstrap_admin()
    if a.demo:
        if a.reset:
            reset_db()
        seed_demo(a.anchor)


if __name__ == "__main__":
    main()
