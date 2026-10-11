<!--
Sync Impact Report
- Version change: 1.0.0 → 1.1.0 (MINOR: new section added)
- Modified principles: none
- Added sections: Code Quality Standards
- Modified sections: Development Workflow & Quality Gates (type-check step added to CI)
- Removed sections: none
- Templates: none modified (dependent templates read the constitution at runtime)
- Deferred TODOs: none
-->

# Football Stats Constitution

## Core Principles

### I. No Silent Data Loss

- Every input row MUST be accounted for: it is either loaded or recorded in a
  `rejected_rows` table with the reason it failed validation.
- Data MUST be validated against an explicit schema (data contract) before it reaches
  the database's clean layers.
- Row counts in vs. rows loaded + rows rejected MUST reconcile and be logged per run.

**Rationale**: Silently dropped data produces wrong analyses that nobody notices. Making every
row traceable is the core habit of trustworthy ETL.

### II. Idempotent, Reproducible Pipelines

- Running any pipeline step twice with the same input MUST produce the same database state
  (no duplicates), e.g. via `ON CONFLICT` upserts or staging-table swaps.
- Raw source data MUST be kept unchanged; cleaning happens in later layers
  (raw → staging → marts), so any layer can be rebuilt from the one before it.
- Pipeline runs MUST be safe to retry after a partial failure.

**Rationale**: Retries, scheduled runs, and incremental loads are only safe when steps are
idempotent. This is a central data-engineering and interview topic.

### III. Tested Against Real Infrastructure

- Cleaning and transformation logic MUST have unit tests.
- Database-touching code MUST have integration tests against a real PostgreSQL instance
  (e.g. testcontainers), not mocks or SQLite.
- API endpoints MUST have tests covering success and error responses.
- A feature is not done until its tests pass in CI.

**Rationale**: Mocked databases hide the bugs that matter (constraints, upserts, SQL dialect).
Tests are a primary learning goal of this project.

### IV. One-Command, Containerized Stack

- The full stack (database, pipeline, API, dashboard) MUST start with `docker compose up`.
- Configuration MUST come from environment variables; secrets MUST NOT be committed.
- Every service MUST run in a container with pinned dependency versions.

**Rationale**: A reviewer or employer should be able to run the project in minutes, and the
same images must work locally, in CI, and on Kubernetes.

### V. Simple, Incremental Delivery

- Work is delivered in phases; each phase MUST end in a working, committed milestone.
- Choose the simplest tool that meets the need; new tools or abstractions MUST be justified
  in the plan by a concrete requirement (YAGNI).
- Learning software/data engineering practice takes priority over analysis depth or
  visual polish.

**Rationale**: A finished, well-engineered small pipeline is worth more than an ambitious,
half-built one.

## Technical Constraints

- **Language & tooling**: Python with a `src/` layout, a lockfile-based package manager
  (`uv` or `poetry`), and `ruff` for linting and formatting.
- **Database**: PostgreSQL. Schema changes MUST be made through versioned migrations
  (Alembic or ordered `.sql` files), never by hand.
- **Schema design**: tables MUST declare primary keys, foreign keys, and appropriate indexes.
- **Transforms**: analytical models live in SQL (dbt or equivalent), layered as
  staging → marts.
- **Data source**: Gábor, A. (2025). *Club Football Match Data*. Attribution MUST appear in
  the README.
- **Platform**: tooling MUST run on macOS (no Windows-only tools such as Power BI Desktop).

## Code Quality Standards

- **Typed code**: all functions in `src/` MUST have type hints and pass a static type checker
  (`mypy` or `pyright`) in CI.
- **Pure logic, separate I/O**: cleaning, validation, and transformation logic MUST live in
  pure functions (data in → data out) with no database, network, or file access, so it can be
  unit-tested without infrastructure.
- **One module per pipeline stage**: each stage (extract, clean, validate, load) lives in its
  own module; circular imports are not allowed.
- **Structured logging**: code MUST use the `logging` module (or a structured logger), not
  `print`; every pipeline run MUST log its row counts (supports Principle I).
- **Explicit error handling**: bare `except:` and swallowed exceptions are not allowed;
  failures MUST either raise or be recorded (e.g. in `rejected_rows`).
- **Docstrings**: public functions MUST have a docstring explaining their purpose and any
  non-obvious behavior.

## Development Workflow & Quality Gates

- Every feature follows the Spec Kit flow: specify → clarify → plan → tasks → implement.
- CI (GitHub Actions) MUST run lint → type check → unit tests → integration tests → image build; a
  failing gate blocks merging.
- Each plan MUST include a Constitution Check confirming compliance with Principles I–V, or
  documenting a justified exception.
- Performance claims (load times, query speedups) MUST be measured (e.g. `EXPLAIN ANALYZE`)
  and recorded, not estimated.
- The README MUST stay current with the architecture, run instructions, and key design
  decisions and trade-offs.

## Governance

- This constitution takes precedence over other project practices and guidance documents.
- Amendments are made via `/speckit-constitution`, with the change and its rationale recorded
  in the Sync Impact Report and the commit message.
- Versioning follows semantic versioning: MAJOR for removed or redefined principles, MINOR
  for new principles or sections, PATCH for clarifications and wording.
- Compliance is checked at plan time (Constitution Check) and at review time; any deviation
  MUST be justified in the plan's complexity tracking.

**Version**: 1.1.0 | **Ratified**: 2026-10-11 | **Last Amended**: 2026-10-11
