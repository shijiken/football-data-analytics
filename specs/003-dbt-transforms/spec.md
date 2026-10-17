# Feature Specification: Analytics Transforms

**Feature Branch**: `003-dbt-transforms`

**Created**: 2026-10-17

**Status**: Draft

**Input**: User description: "Phase 3 of PLAN.md — transform layer: a dbt project with staging → marts models over the ingested match data. Example marts: team form, home advantage by league, goals per season, Elo ratings. Practice window functions and CTEs. Use EXPLAIN ANALYZE, add indexes, measure and document speedups."

## Context

Features 001 and 002 load ~237k validated, contract-checked matches into normalized tables
(`leagues`, `teams`, `matches`). Analysts (and the Phase 5 API) need answers such as "which
leagues have the strongest home advantage?" or "what is a team's recent form?" without each
consumer re-deriving them from raw matches. The constitution requires analytical models to live
in SQL, layered staging → marts, so every layer can be rebuilt from the one before it.

## User Scenarios & Testing *(mandatory)*

The primary user is the project developer (and later the API and dashboard) who needs
trustworthy, ready-to-query analytical tables.

### User Story 1 - Build analytics tables with one command (Priority: P1)

After ingestion, the developer runs one command that builds a staging layer (cleaned,
analysis-friendly views of matches with derived fields such as season and points) and a marts
layer of analytical tables, and runs data tests on them. The build fails if any data test fails.

**Why this priority**: Every analytical question and the future API depend on these tables.

**Independent Test**: Load a small known fixture, run the build, and confirm every model exists
and all data tests pass; rerun it and confirm identical contents.

**Acceptance Scenarios**:

1. **Given** a database loaded by the ingestion pipeline, **When** the developer runs the
   transform command, **Then** staging and mart models are created in schemas separate from the
   ingestion tables, and all data tests pass.
2. **Given** the transform has been built, **When** it is run again without data changes,
   **Then** every model has identical contents.
3. **Given** a model's data test fails (e.g. a duplicate key), **When** the build runs, **Then**
   the command exits non-zero and names the failing test.
4. **Given** the transform has run, **When** ingestion tables are inspected, **Then** they are
   unchanged (transforms only read them).

---

### User Story 2 - Answer the core analytical questions (Priority: P1)

The developer can query four marts:

- **Team form**: for every match a team played, its points and goals in that match and its
  rolling form over its previous 5 matches (points, goals for/against), in date order.
- **Home advantage by league and season**: share of home wins, draws, and away wins, average
  home and away goals, and matches played.
- **Goals per season**: per league and season, total goals, goals per match, and share of
  matches with over 2.5 goals and with both teams scoring.
- **Team ratings**: per team, the latest, peak, and lowest pre-match Elo rating from the
  source data, with dates.

**Why this priority**: These are the analyses the project exists for.

**Independent Test**: For a fixture with hand-calculated expected results, every mart value
matches the expected value exactly.

**Acceptance Scenarios**:

1. **Given** a team with matches on known dates, **When** the developer queries team form,
   **Then** each row's rolling form covers only that team's previous (strictly earlier) matches,
   at most 5, and is empty for its first match.
2. **Given** a league-season, **When** the developer queries home advantage, **Then** home-win,
   draw and away-win shares sum to 100% (within rounding) and the match count equals the number
   of loaded matches for that league-season.
3. **Given** a league-season, **When** the developer queries goals per season, **Then** totals
   equal the sum of full-time goals of its matches.
4. **Given** a team with Elo ratings on several dates, **When** the developer queries team
   ratings, **Then** latest, peak, and lowest values and their dates are correct; a team with no
   ratings has no rating values.

---

### User Story 3 - Fast, measured queries (Priority: P2)

The developer knows the most important analytical queries are fast, with before/after
measurements of any index added for them.

**Why this priority**: Required by the constitution (performance claims are measured), and a
core learning goal; the marts are usable without it.

**Independent Test**: The README contains a table of measured query timings before and after
each index, captured from query plans on the full dataset.

**Acceptance Scenarios**:

1. **Given** the full dataset, **When** the developer reads the README, **Then** it lists each
   index added for this feature, the query it serves, and measured execution times before and
   after, taken from real query plans.
2. **Given** the index changes, **When** they are applied, **Then** they are applied as
   versioned structure changes, never by hand.

---

### User Story 4 - Run it in the containerized stack and CI (Priority: P2)

The transform runs as a container in the project stack after ingestion, and CI runs the
transform's data tests against a real database.

**Why this priority**: The one-command, containerized principle; useful but secondary to the
models themselves.

**Independent Test**: In a clean environment, start the stack, ingest a fixture, run the
transform service, and confirm it succeeds; CI runs the same transform tests.

**Acceptance Scenarios**:

1. **Given** the stack with ingested data, **When** the transform service runs, **Then** it
   builds and tests every model and exits 0.
2. **Given** a pull request, **When** CI runs, **Then** the transform models are built and
   tested against a real database loaded with fixtures.

---

### Edge Cases

- A team plays twice on the same date (rare data quirk): rolling form must still be
  deterministic (order by date, then a stable tie-breaker).
- A league whose season runs within a calendar year (e.g. Argentina, USA) vs August–May leagues:
  the season label rule must be documented and applied consistently.
- A league-season with zero draws or zero away wins: shares are 0, not missing.
- Matches with missing optional data (e.g. no Elo): excluded from rating aggregates only, still
  counted elsewhere.
- An empty database (no matches): the build succeeds and marts are empty.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a transform project whose models are SQL, organized in a
  staging layer (one model per source entity, light renaming and derivations only) and a marts
  layer (analytical tables), in database schemas separate from the ingestion tables.
- **FR-002**: Staging MUST derive, per match: season label, result from each side's
  perspective, points (3 win / 1 draw / 0 loss), and total goals.
- **FR-003**: The season label rule MUST be: matches in July–December belong to season
  "YYYY/YY+1", January–June to "YYYY-1/YY", applied to all leagues; the rule MUST be documented.
- **FR-004**: The marts MUST include team form, home advantage by league-season, goals per
  league-season, and team ratings, as described in User Story 2.
- **FR-005**: Team form MUST be computed with window functions over strictly earlier matches of
  the same team, ordered by date then a stable tie-breaker, covering at most 5 previous matches.
- **FR-006**: Every model MUST have data tests for its key (unique, not null) and its
  relationships; marts MUST also have tests of their business invariants (e.g. shares sum to
  100%, totals match staging).
- **FR-007**: Running the transform twice with unchanged inputs MUST produce identical
  contents; running it MUST NOT modify ingestion tables.
- **FR-008**: The transform MUST be runnable with one command locally and as a service in the
  containerized stack, configured only via environment variables.
- **FR-009**: Mart values MUST be verified by automated tests against a fixture with
  hand-calculated expected results, run against a real database in the quality gate and CI.
- **FR-010**: Any index added for analytical queries MUST be a versioned structure change, and
  its effect MUST be measured on the full dataset (before/after) and recorded in the README.

### Key Entities *(include if feature involves data)*

- **Staged Match**: one match with season, points and results per side.
- **Team-Match**: one row per team per match (two per match), the base of team form.
- **Team Form**: a team-match plus rolling previous-5 aggregates.
- **League Season Summary**: home advantage and goal statistics per league and season.
- **Team Rating Summary**: latest, peak and lowest Elo per team.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A full transform build over the real dataset completes in under 2 minutes on a
  typical laptop (measured and recorded).
- **SC-002**: 100% of mart values in the fixture tests match hand-calculated expectations.
- **SC-003**: 0 data-test failures on the real dataset.
- **SC-004**: Two consecutive builds produce identical contents in 100% of models.
- **SC-005**: Each index added shows a measured before/after timing in the README; no
  unmeasured performance claims.

## Assumptions

- **User-mandated technology** (PLAN.md and the constitution): the transform layer is dbt with
  the PostgreSQL adapter; marts are materialized as tables, staging as views.
- **Versioned structure changes (FR-010)**: for dbt-owned schemas, the dbt model definition
  (including its `indexes` and contract constraints) is the versioned structure change; schema
  changes in `public` remain Alembic migrations.
- Elo ratings come from the source data's pre-match Elo columns; computing our own Elo is out of
  scope.
- A single season rule (July cut-over) is acceptable for calendar-year leagues; their labels
  will span two years. A per-league season calendar is out of scope.
- Marts are rebuilt in full each run; incremental models are out of scope at this data size.
- Orchestration (scheduling transform after ingest) is Phase 4; here the transform is run
  explicitly.
- The API and dashboard that consume the marts are out of scope.
