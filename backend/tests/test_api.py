"""One API test per router, plus the 6-step demo script from section 9 end to end."""
from datetime import timedelta

from app import seed
from app.services import metrics
from tests.conftest import login


def W(i):
    return (metrics.current_week() + timedelta(weeks=i)).isoformat()


def test_health_and_auth(client):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/auth/login", json={"email": "gl@demo", "password": "wrong"}).status_code == 401
    h = login(client, "gl")
    me = client.get("/api/me", headers=h).json()
    assert me["email"] == "gl@demo" and "gl" in me["roles"]
    assert len(client.get("/api/auth/config").json()["demo_users"]) >= 4


def test_people_router(client, as_user):
    h = as_user("gl")
    people = client.get("/api/people", headers=h).json()
    assert len(people) == 60
    pid = people[0]["id"]
    tl = client.get(f"/api/people/{pid}/timeline", headers=h).json()
    assert len(tl["weeks"]) == 16  # 4 back + 12 forward
    own = next(p for p in people if p["can_edit"])
    r = client.put(f"/api/people/{own['id']}/skills", headers=h, json=[{"name": "SQL", "proficiency": 3}, {"name": "Kafka", "proficiency": 1}])
    assert r.status_code == 200 and {s["name"] for s in r.json()} == {"SQL", "Kafka"}
    other = next(p for p in people if not p["can_edit"])
    assert client.put(f"/api/people/{other['id']}/skills", headers=h, json=[]).status_code == 403
    assert client.post(f"/api/people/{own['id']}/availability", headers=h, json={"from_date": W(1), "hours_per_week": 16}).status_code == 200
    assert client.post(f"/api/people/{own['id']}/capacity-exceptions", headers=h, json={"date": W(2), "hours_off": 8, "days": 2}).status_code == 200
    hist = client.get(f"/api/people/{own['id']}/history", headers=h).json()
    assert any(c["entity"] == "person_skill" for c in hist)


def test_bill_rates_stripped_for_members(client, as_user):
    admin = as_user("admin")
    r = client.post("/api/admin/users", headers=admin, json={"email": "member@demo", "name": "Mo Member", "password": "demo1234", "roles": [{"role": "member"}]})
    assert r.status_code == 200
    m = login(client, "member")
    data = client.get(f"/api/people?available_from={W(0)}&available_to={W(3)}", headers=m).json()
    assert data["people"] and all("bill_rate" not in p for p in data["people"])
    assert all("bill_rate" in p for p in client.get(f"/api/people?available_from={W(0)}&available_to={W(3)}", headers=as_user("pl")).json()["people"])
    assert client.get("/api/admin/rate-cards", headers=m).status_code == 403


def test_projects_router(client, as_user):
    h = as_user("pl")
    r = client.post("/api/projects", headers=h, json={"name": "New Pitch", "client": "Contoso", "status": "pipeline"})
    assert r.status_code == 200
    pid = r.json()["id"]
    levels = {t["code"]: t["id"] for t in client.get("/api/tech-levels", headers=h).json()}
    r = client.post(f"/api/projects/{pid}/demand", headers=h, json={"role_family": "Analyst", "min_tech_level_id": levels["T4"], "skills": ["SQL"], "hours_per_week": 20, "start_week": W(2), "end_week": W(6), "confidence": 50})
    assert r.status_code == 200 and r.json()["weighted_hours_per_week"] == 10
    dl = r.json()["id"]
    assert len(client.get(f"/api/demand/{dl}/matches", headers=h).json()) <= 5
    assert client.post(f"/api/projects/{pid}/demand", headers=as_user("pl2"), json={"hours_per_week": 5, "start_week": W(1), "end_week": W(2)}).status_code == 403
    assert client.patch(f"/api/demand/{dl}", headers=h, json={"confidence": 60}).status_code == 422


def test_allocations_router(client, as_user):
    h = as_user("gl")
    grid = client.get("/api/allocations/grid", headers=h).json()
    row = next(r for r in grid["rows"] if r["editable"])
    r = client.patch("/api/allocations/weeks", headers=h, json={"edits": [{"allocation_id": row["allocation_id"], "week_start": W(1), "hours": 12}], "reason": "test"})
    assert r.status_code == 200 and r.json()["changed"] == 1
    assert row["person_id"] in r.json()["person_status"]
    hist = client.get(f"/api/allocations/{row['allocation_id']}/history", headers=h).json()
    assert hist[0]["old_value"] is not None and hist[0]["new_value"] == "12" and hist[0]["actor"] == "gl@demo" and hist[0]["reason"] == "test"
    r = client.post("/api/allocations/bulk", headers=h, json={"allocation_id": row["allocation_id"], "frm": W(3), "to": W(5), "hours": 20})
    assert r.status_code == 200 and r.json()["changed"] >= 1
    pending = client.get("/api/allocations/pending-openair", headers=h).json()
    assert any(p["week_start"] == W(1) for p in pending)
    assert "text/csv" in client.get("/api/allocations/pending-openair?format=csv", headers=h).headers["content-type"]
    # reassign to another project
    proj = next(p for p in client.get("/api/projects", headers=h).json() if p["id"] != row["project_id"] and p["status"] == "active")
    r = client.post("/api/allocations/reassign", headers=h, json={"allocation_id": row["allocation_id"], "to_project_id": proj["id"], "frm": W(3), "to": W(5)})
    assert r.status_code == 200 and r.json()["moved_hours"] == 60
    # a PL cannot edit a GL's person on another PL's project
    pl_grid = client.get("/api/allocations/grid", headers=as_user("pl2")).json()
    foreign = next(r for r in grid["rows"] if r["allocation_id"] and r["allocation_id"] not in {x["allocation_id"] for x in pl_grid["rows"] if x["editable"]})
    assert client.patch("/api/allocations/weeks", headers=as_user("pl2"), json={"edits": [{"allocation_id": foreign["allocation_id"], "week_start": W(1), "hours": 1}]}).status_code == 403


def test_dashboard_and_heatmap_router(client, as_user):
    h = as_user("gl")
    gl = client.get("/api/dashboard/gl", headers=h).json()
    assert gl["headcount"] == 16 and gl["hot"] and gl["light"]
    assert client.get("/api/dashboard/pl", headers=as_user("pl")).json()["projects"]
    lead = client.get("/api/dashboard/leadership", headers=as_user("sl")).json()
    assert lead["supply_demand"] and lead["billable_by_group"]
    hm = client.get("/api/heatmap?level=person&mode=confirmed", headers=h).json()
    assert len(hm["rows"]) == 60 and len(hm["weeks"]) == 12
    cell = client.get(f"/api/heatmap/cell?person={hm['rows'][0]['id']}&week={W(0)}", headers=h).json()
    assert "allocations" in cell
    g = client.get("/api/heatmap?level=group", headers=h).json()["rows"][0]
    assert "people" in client.get(f"/api/heatmap/cell?group={g['id']}&week={W(0)}", headers=h).json()


def test_alerts_router(client, as_user):
    h = as_user("gl")
    feed = client.get("/api/alerts", headers=h).json()
    assert feed["unread"] > 0
    aid = feed["items"][0]["id"]
    assert client.patch(f"/api/alerts/{aid}", headers=h, json={"status": "done"}).status_code == 200
    assert client.patch("/api/alerts", headers=h, json={"all": True, "status": "seen"}).json()["updated"] >= 1


def test_imports_router(client, as_user):
    h = as_user("admin")
    assert client.post("/api/imports/upload", headers=as_user("gl"), files={"file": ("d.csv", b"a,b\n1,2\n")}).status_code == 403
    r = client.post("/api/imports/upload", headers=h, files={"file": ("renamed.csv", b"Who,When\nx,y\n")}, data={"auto_run": "true"})
    assert r.status_code == 200 and r.json()["missing_required"] and "run" not in r.json()
    assert client.post("/api/imports/run", headers=h, json={"raw_file_id": r.json()["raw_file_id"], "mapping": {"user_name": "Who"}}).status_code == 422
    assert len(client.get("/api/imports", headers=h).json()) >= 1
    assert client.get("/api/imports/conflicts", headers=h).status_code == 200
    assert client.post("/api/imports/run", headers=h).status_code == 200  # scheduled pull, nothing in inbox


def test_admin_and_audit_router(client, as_user):
    h = as_user("admin")
    s = client.get("/api/admin/settings", headers=h).json()
    assert s["bands"]["hot"] == 115
    r = client.put("/api/admin/settings", headers=h, json={"bands": {"bench": 45, "light": 80, "healthy": 100, "hot": 120}})
    assert r.status_code == 200 and r.json()["bands"]["bench"] == 45
    assert client.put("/api/admin/settings", headers=h, json={"bands": {"bench": 90, "light": 80, "healthy": 100, "hot": 120}}).status_code == 422
    assert client.put("/api/admin/settings", headers=as_user("sl"), json={"visibility_mode": "scoped"}).status_code == 403
    cards = client.get("/api/admin/rate-cards", headers=h).json()
    cards[0]["rate_usd_hr"] = 99
    assert client.put("/api/admin/rate-cards", headers=h, json=cards).status_code == 200
    assert client.get("/api/admin/users", headers=h).status_code == 200
    log = client.get("/api/audit?entity=setting", headers=h).json()
    assert log and log[0]["actor"] == "admin@demo"
    for view in ("heatmap", "grid", "availability", "leadership", "people", "projects", "audit"):
        r = client.get(f"/api/export/{view}.xlsx", headers=h)
        assert r.status_code == 200 and r.content[:2] == b"PK", view


def test_requests_router_decline(client, as_user):
    pl2 = as_user("pl2")
    avail = client.get(f"/api/people?available_from={W(1)}&available_to={W(4)}&level=T5&skill=SQL", headers=pl2).json()["people"]
    prj = next(p for p in client.get("/api/projects?mine=true", headers=pl2).json() if p["status"] == "active")
    r = client.post("/api/requests", headers=pl2, json={"person_id": avail[0]["person_id"], "project_id": prj["id"], "hours_per_week": 8, "start_week": W(1), "end_week": W(2)})
    assert r.status_code == 200
    rid = r.json()["id"]
    gl_email = None
    for who in ("gl", "gl.a", "gl.c", "gl.d", "gl.e"):
        if any(x["id"] == rid for x in client.get("/api/requests?box=inbox", headers=login(client, who)).json()):
            gl_email = who
    assert gl_email
    h = login(client, gl_email)
    assert client.patch(f"/api/requests/{rid}", headers=h, json={"action": "decline"}).status_code == 422
    r = client.patch(f"/api/requests/{rid}", headers=h, json={"action": "decline", "reason": "On leave"})
    assert r.json()["status"] == "declined"


def test_demo_script_end_to_end(client, as_user):
    """Section 9 demo script, steps 2-6."""
    admin, gl, pl, pl2, sl = (as_user(x) for x in ("admin", "gl", "pl", "pl2", "sl"))
    # 2. Import: yesterday's export (already loaded) then today's -> only the deltas
    r1 = client.post("/api/imports/upload", headers=admin, files={"file": ("openair_export_day1.csv", seed.sample_csv(1).encode())}).json()
    assert r1["run"]["status"] == "skipped"
    r2 = client.post("/api/imports/upload", headers=admin, files={"file": ("openair_export_day2.csv", seed.sample_csv(2).encode())}).json()
    assert (r2["run"]["changed"], r2["run"]["new"], r2["run"]["removed"]) == (14, 2, 1)

    # 3. GL view: Group B hot in weeks 3-6, driver says project spike from one client
    hm = client.get("/api/heatmap?level=group&mode=forecast", headers=gl).json()
    b = next(r for r in hm["rows"] if r["name"].startswith("Group B"))
    for cell in b["cells"][2:6]:
        assert cell["status"] in ("hot", "over")
        assert cell["driver"]["driver"] == "project_spike" and cell["driver"]["top_project_name"] == "Retail Data Platform"
    assert b["cells"][0]["status"] == "healthy"
    c = next(r for r in hm["rows"] if r["name"].startswith("Group C"))
    assert c["cells"][0]["driver"]["driver"] == "structural_gap"

    # 4. Project falls through: PL sets the December project to 0% -> people turn Light, GL alerted
    prj = next(p for p in client.get("/api/projects?mine=true", headers=pl).json() if p["client"] == "Northwind Traders")
    detail = client.get(f"/api/projects/{prj['id']}", headers=pl).json()
    people = {a["person_id"] for a in detail["allocations"]}
    assert len(people) == 3
    before = client.get(f"/api/heatmap?level=person&mode=forecast&frm={W(2)}&to={W(5)}", headers=gl).json()
    assert all(r["cells"][0]["status"] == "healthy" for r in before["rows"] if r["id"] in people)
    prompts = [client.patch(f"/api/demand/{dl['id']}", headers=pl, json={"confidence": 0}).json() for dl in detail["demand"]]
    assert all(p["release_prompt"]["count"] == 1 for p in prompts)
    for dl in detail["demand"]:
        assert client.post(f"/api/demand/{dl['id']}/release", headers=pl).status_code == 200
    after = client.get(f"/api/heatmap?level=person&mode=forecast&frm={W(2)}&to={W(5)}", headers=gl).json()
    assert all(r["cells"][0]["status"] == "light" for r in after["rows"] if r["id"] in people)
    alerts = client.get("/api/alerts", headers=gl).json()["items"]
    assert any(a["type"] == "project_zero" for a in alerts)
    assert sum(1 for a in alerts if a["type"] == "status_light" and a["person_id"] in people) == 3

    # 5. Re-staff: another PL filters Tech 5 + SQL, requests one person; GL accepts; grid updates
    avail = client.get(f"/api/people?available_from={W(1)}&available_to={W(9)}&level=T5&skill=SQL&min_free=10", headers=pl2).json()["people"]
    cand = next(p for p in avail if p["group_name"].startswith("Group B"))
    c360 = next(p for p in client.get("/api/projects?mine=true", headers=pl2).json() if p["client"] == "Contoso")
    dl = next(d for d in client.get(f"/api/projects/{c360['id']}/demand", headers=pl2).json() if d["status"] == "open")
    matches = client.get(f"/api/demand/{dl['id']}/matches", headers=pl2).json()
    assert any(m["person_id"] == cand["person_id"] for m in matches)
    req = client.post("/api/requests", headers=pl2, json={"kind": "request", "person_id": cand["person_id"], "project_id": c360["id"], "demand_line_id": dl["id"], "hours_per_week": 20, "start_week": W(1), "end_week": W(9)}).json()
    assert req["pl_status"] == "accepted" and req["gl_status"] == "pending"
    inbox = client.get("/api/requests?box=inbox", headers=gl).json()
    assert any(x["id"] == req["id"] for x in inbox)
    done = client.patch(f"/api/requests/{req['id']}", headers=gl, json={"action": "accept"}).json()
    assert done["status"] == "confirmed"
    grid = client.get(f"/api/allocations/grid?person={cand['person_id']}", headers=gl).json()
    row = next(r for r in grid["rows"] if r["project_id"] == c360["id"])
    assert row["state"] == "confirmed" and row["weeks"][W(1)] == 20
    assert next(d for d in client.get(f"/api/projects/{c360['id']}/demand", headers=pl2).json() if d["id"] == dl["id"])["status"] == "filled"

    # 6. Leadership view: billable utilization by group; supply vs weighted demand by Tech level
    lead = client.get("/api/dashboard/leadership", headers=sl).json()
    assert len(lead["billable_by_group"]) == 5 and all(g["billable_util"] is not None for g in lead["billable_by_group"])
    assert {r["tech_level"] for r in lead["supply_demand"]} >= {"T4", "T5", "T6"}
    t5 = next(r for r in lead["supply_demand"] if r["tech_level"] == "T5")
    assert t5["supply_hours"] > 0 and t5["weighted_demand_hours"] > 0
