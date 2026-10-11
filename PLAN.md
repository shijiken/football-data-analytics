# ⚽ Football Stats — Project Plan

A data engineering project that processes football match data (240k+ rows) from a raw CSV, validates and cleans it, loads it into PostgreSQL, transforms it into analysis-ready models, and serves it through an API and dashboard — all containerized and orchestrated.

**Goal:** learn software engineering / data engineering practice (Docker, Kubernetes, ETL pipelines, testing, CI). Data analysis and visualization are secondary.

**Data source:** Gábor, A. (2025). *Club Football Match Data.* https://github.com/xgabora/Club-Football-Match-Data-2000-2025/

---

## Architecture

```
CSV (GitHub) ──► Extract ──► Validate/Clean ──► Load (Postgres) ──► Transform (SQL/dbt) ──► Serve
                  Python       pandas/polars      raw → staging        marts/views          FastAPI + dashboard
                                + pandera
        └──────────── orchestrated by Prefect or Airflow, all in Docker Compose ─────────────┘
                                    CI: GitHub Actions (lint, tests, build images)
                                    Stretch: deploy to Kubernetes (kind/k3d locally)
```

## Tech stack

| Layer | Tool |
|---|---|
| Project tooling | Python, `uv` (or poetry), `ruff`, `src/` layout |
| Data processing | polars or pandas |
| Validation | pandera (or Great Expectations) |
| Database | PostgreSQL, SQLAlchemy / psycopg |
| Migrations | Alembic (or plain `.sql` files) |
| Transforms | dbt-postgres |
| Orchestration | Prefect (lighter) or Airflow (more resume keywords) |
| API | FastAPI |
| Tests | pytest + testcontainers |
| Containers | Docker, Docker Compose |
| CI | GitHub Actions |
| Visualization | Metabase / Superset (container) or Streamlit |
| Stretch | Kubernetes via kind or k3d, Helm |

> Note: Power BI Desktop is Windows-only — on macOS use Metabase, Superset, or Streamlit instead.

---

## Phases

Each phase ends in a working, committable milestone.

### Phase 1 — Foundations (week 1)
- [ ] Set up Python project (`uv`/`poetry`, `src/` layout, `ruff`, `pytest`)
- [ ] `extract.py` — download the CSV
- [ ] `clean.py` — handle nulls, normalize team names, parse dates, dedupe
- [ ] `load.py` — write to Postgres
- [ ] Postgres service in `docker-compose.yml`
- [ ] Schema as migrations (teams, leagues, matches; primary/foreign keys; indexes)

**Learn:** relational schema design, Docker basics.

### Phase 2 — Make it correct (week 2)
- [ ] Data validation with pandera; failed rows go to a `rejected_rows` table (never silently dropped)
- [ ] **Idempotent loads** — running the pipeline twice must not duplicate data (upserts via `ON CONFLICT`, or staging table + swap)
- [ ] Unit tests for cleaning functions
- [ ] Integration tests against a real Postgres via testcontainers

**Learn:** idempotency, data contracts, testing — core SWE interview topics.

### Phase 3 — Transform layer (week 3)
- [ ] dbt project with `staging → marts` models
- [ ] Example marts: team form, home advantage by league, goals per season, Elo ratings
- [ ] Practice window functions and CTEs
- [ ] Use `EXPLAIN ANALYZE`, add indexes, measure and document speedups

**Learn:** analytical SQL, query performance.

### Phase 4 — Orchestration & containers (week 4)
- [ ] Containerize the pipeline (multi-stage Dockerfile, small image)
- [ ] Orchestrate with Prefect or Airflow: retries, logging, scheduled runs
- [ ] `docker compose up` brings up the entire stack in one command

**Learn:** Dockerfiles, orchestration, operational concerns.

### Phase 5 — SWE showcase (week 5)
- [ ] FastAPI service over the marts, e.g.
  - `GET /teams/{id}/form`
  - `GET /leagues/{id}/table?season=`
- [ ] Pagination, OpenAPI docs, API tests
- [ ] GitHub Actions CI: lint → unit tests → integration tests (Postgres service) → build Docker images
- [ ] Dashboard (Metabase/Superset container or Streamlit app)

**Learn:** API design, CI/CD.

### Phase 6 — Stretch: Kubernetes
- [ ] Local cluster with kind or k3d
- [ ] Deploy Postgres, the API, and the pipeline as a **CronJob**
- [ ] Start with plain manifests, then optionally convert to Helm

**Learn:** K8s fundamentals. Do this last — Phases 2 and 5 matter more for an intern resume.

---

## Making it stand out

- **README** with architecture diagram, one-command run instructions, design decisions & trade-offs (e.g. "why upserts over truncate-reload"), and measured numbers (load time, query speedups).
- **Make it your own** — same dataset as the reference project, but the API, tests, and orchestration differentiate it.
- **Incremental loads** — simulate new data arriving season-by-season; the hardest and most impressive part of real ETL.

## Suggested repo layout

```
football-stats/
├─ src/football_stats/
│  ├─ extract.py
│  ├─ clean.py
│  ├─ validate.py
│  ├─ load.py
│  └─ flows.py          # Prefect/Airflow orchestration
├─ api/                 # FastAPI app
├─ dbt/                 # staging + marts models
├─ migrations/
├─ tests/
│  ├─ unit/
│  └─ integration/
├─ k8s/                 # stretch
├─ Dockerfile
├─ docker-compose.yml
├─ .github/workflows/ci.yml
├─ pyproject.toml
└─ README.md
```
