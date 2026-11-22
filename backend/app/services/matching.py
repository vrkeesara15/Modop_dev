"""AVL-3: suggest the top matches for an open demand line."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Allocation, DemandLine, Project, TechLevel
from app.services import metrics
from app.services.rules import margin_warning, match_score


def suggest(db: Session, dl: DemandLine, limit: int = 5) -> list[dict]:
    levels = {t.id: t for t in db.scalars(select(TechLevel))}
    min_rank = levels[dl.min_tech_level_id].rank if dl.min_tech_level_id in levels else None
    project = db.get(Project, dl.project_id)
    avail = metrics.availability(db, dl.start_week, dl.end_week, min_free=0.01)
    # current-project continuity: people already on this project or this client's projects
    on_project = set(db.scalars(select(Allocation.person_id).where(Allocation.project_id == dl.project_id, Allocation.is_deleted.is_(False), Allocation.state != "released")))
    client_projects = set(db.scalars(select(Project.id).where(Project.client == project.client))) if project and project.client else set()
    on_client = set(db.scalars(select(Allocation.person_id).where(Allocation.project_id.in_(client_projects), Allocation.is_deleted.is_(False)))) if client_projects else set()
    rate = metrics.rate_lookup(db)
    req_rate = rate(dl.min_tech_level_id, dl.role_family)
    out = []
    for a in avail:
        skills = {s["name"].lower(): s["proficiency"] for s in a["skills"]}
        import uuid

        pid = uuid.UUID(a["person_id"])
        score = match_score(
            person_rank=a["tech_level_rank"],
            min_rank=min_rank,
            person_skills=skills,
            wanted_skills=dl.skills or [],
            free_hours=a["free_hours_avg"],
            wanted_hours=float(dl.hours_per_week),
            continuity=pid in on_project or pid in on_client,
            role_match=not dl.role_family or (a["role_family"] or "").lower() == dl.role_family.lower(),
        )
        if score <= 0:
            continue
        out.append(
            {
                **a,
                "score": score,
                "skill_hits": [s for s in (dl.skills or []) if s.lower() in skills],
                "continuity": pid in on_project or pid in on_client,
                "margin_warning": margin_warning(req_rate, a["bill_rate"]),
            }
        )
    out.sort(key=lambda r: (-r["score"], r["name"]))
    return out[:limit]
