# Resource Allocation Hub

A web app that replaces the Excel + Teams staffing process. It imports the daily OpenAir export, shows who is busy, hot, light or on the bench week by week, lets Group Leaders (GL) and Project Leaders (PL) edit and reassign hours in an Excel-like grid, and runs the GL + PL request/accept handshake for staffing.

Built from *Resource Allocation Hub — Requirements Document* (Nov 20, 2026).

| Layer | Stack |
|---|---|
| Backend | FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, APScheduler |
| Frontend | React 18, Vite, Tailwind, shadcn-style UI, AG Grid Community, Recharts, TanStack Query, React Router |
| Packaging | One multi-stage `Dockerfile`, `docker-compose.yml`, `railway.json` |

## Quick start (Docker)

```bash
cp .env.example .env          # optional; defaults work for a local demo
docker compose up --build
open http://localhost:8000
```

On first start the container runs `alembic upgrade head`, then, because `SEED_DEMO=true`, seeds demo data (60 people in 5 groups, 20 projects, 12 weeks) by pushing a sample OpenAir export through the real import pipeline. Seeding only happens when the database has no users.

Demo logins (password `demo1234`, override with `DEMO_PASSWORD`):

| Email | Role |
|---|---|
| `admin@demo` | Admin (imports, settings, users, override) |
| `sl@demo` | Service-line lead (leadership view, can change bands) |
| `gl@demo` | Gina Morales, GL of Group B - Data Engineering |
| `gl.a@demo` … `gl.e@demo` | GLs of the other groups |
| `pl@demo`, `pl2@demo`, `pl3@demo` | Project Leaders |

Optional S3-compatible storage for raw files: `docker compose --profile s3 up` and set `STORAGE_BACKEND=s3`.

## Deploy on Railway

1. Create a project from this GitHub repo. Railway picks up `railway.json` (Dockerfile build, health check on `/healthz`).
2. Add the **PostgreSQL** plugin. Railway injects `DATABASE_URL`; `postgres://` URLs are normalised automatically.
3. Set variables: `SECRET_KEY` (long random string), `APP_BASE_URL` (the public URL), `COOKIE_SECURE=true`, and either `SEED_DEMO=true` for the demo or `ADMIN_EMAIL` + `ADMIN_PASSWORD` for a clean install.
4. Optional: attach a volume at `/data` for raw import files (or use `STORAGE_BACKEND=s3`), and fill in SMTP / Teams / Entra ID / OpenAir variables from `.env.example`.

Railway sets `PORT`; the entrypoint binds to it.

## Local development (without Docker)

```bash
# backend
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://postgres@localhost:5432/rah STORAGE_LOCAL_PATH=./data
alembic upgrade head
python -m app.seed --demo            # add --reset to wipe and reseed
uvicorn app.main:app --reload        # http://localhost:8000

# frontend (dev server proxies /api to :8000)
cd frontend && npm ci && npm run dev  # http://localhost:5173
```

Tests: `cd backend && pytest` (needs a Postgres database; defaults to `rah_test` on localhost, override with `DATABASE_URL`). The suite covers the rules engine, the import pipeline (diff, SCD2, conflicts, rollback) and the API, including the full demo script.

## Demo script (spec §9)

1. **Import** — Admin → *Imports*: the seed has already loaded *yesterday's* export (Day 1). Download the *Day 2* sample export and upload it. The summary shows **14 changed, 2 new, 1 removed**.
2. **GL view** — sign in as `gl@demo` → *Capacity heatmap*: Group B is **Hot in December**; the driver reads **"Project spike: Retail Data Platform"**. Group C is hot too, but as a **structural gap** (spread across projects).
3. **Project falls through** — sign in as `pl@demo` → *Projects & demand* → *Holiday Demand Analytics (Northwind)*: set confidence to **0%** and release. Three people turn **Light**, and `gl@demo` gets the alert (bell icon).
4. **Re-staff** — sign in as `pl2@demo` → *Availability board*: filter **Tech 5 + SQL**, request a person for the open Contoso line. `gl@demo` accepts in *Requests inbox*; the request is Confirmed, the demand line becomes Filled and the grid updates.
5. **Leadership view** — sign in as `sl@demo` → *Home*: billable utilization by group, and supply vs weighted demand by Tech level.

## What is built

| Module | Highlights |
|---|---|
| IMP Import | CSV/XLSX upload, column mapping saved per report type with auto-suggest, validation with downloadable rejects, staging, diff by business key + row hash, SCD2 history, change log, in-app edit protection (conflicts queue), full rollback on failure, checksum skip, scheduled pull from a watched folder/storage inbox, optional OpenAir REST pull |
| PPL People | Directory, profile, skills with proficiency, capacity exceptions (PTO/part-time), availability flags, history |
| DEM Demand | Projects and demand lines with confidence steps, app-only pipeline projects linked when they appear in OpenAir, mark lost / release, candidate matching |
| ALC Allocation | Editable grid (inline edit, Excel paste, undo, bulk fill, reassign, history), allocation states Proposed → Requested → Confirmed → Released/Declined, pending-OpenAir export |
| AVL Availability | Free hours by week, filters by level/skill/group/date range, one-click request |
| DSH Dashboards | GL, PL and leadership dashboards, capacity heatmap with drill-down, hot-team driver |
| ALR Alerts | Status transitions, unfilled demand, roll-offs, zero-confidence projects, daily digest (SMTP / Teams) |
| ADM Admin | Settings (bands, confidence steps, weights, visibility), rate cards, users and roles, groups, audit log, Excel exports |

Auth: local email + password (bcrypt, JWT cookie) or Microsoft Entra ID (`AUTH_MODE=oidc`).

## Assumptions and decisions

- **History tables.** SCD2 history is kept in one generic `record_history` table (entity, key, valid_from/valid_to, row JSON) and staging in one `stg_openair_rows` table, instead of one history/staging table per entity. Same behaviour, fewer tables.
- **Derived views** (person-week utilization, group roll-ups) are computed by Python services on demand rather than stored as SQL views, so the rules live in one tested module (`app/services/rules.py`).
- **Imports never delete people or projects.** A full-snapshot bookings import soft-deletes bookings missing from the file; people and projects missing from a file are left alone.
- **Hot-team driver.** For each hot group-week, each project's hours are compared with that project's median weekly hours for the group across the window. If the single largest excess explains more than the spike threshold (default 60%) of the overload, the driver is a *project spike* on that project; otherwise it is a *structural gap*.
- **Weighting.** Confirmed work counts 100%. Proposed/requested work on a demand line counts at the line's confidence. Soft bookings and proposed work without a line count at `soft_booking_weight` (0.5). Lost, on-hold and closed projects count 0%.
- **Bookings to weeks.** OpenAir bookings with a date range are spread across working days and summed by Monday-start week. Bulk fill sets full weeks touching the range.
- **Grid rows.** Bookings for the same person, project, state and demand line are shown on one row when their weeks don't overlap (overlapping bookings such as a surge on top of a base booking stay as separate rows). Edits go to the booking that owns that week.
- **Copy/paste** in the grid is custom code, because AG Grid range selection is an Enterprise feature.
- **OpenAir REST.** The REST pull (`OPENAIR_*` variables) follows the generic OpenAir REST report pattern; the exact endpoint and report ID need confirming with the OpenAir admin. The watched-folder import works without it.
- **Not built yet:** DEM-6 *Forecast accuracy* (P2) — it needs monthly forecast snapshots, which can be added on top of `record_history`.
