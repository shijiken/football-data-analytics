# Research: Analytics Transforms

**Feature**: `003-dbt-transforms` | **Date**: 2026-10-17 | **Plan**: [plan.md](./plan.md)

Every technology choice and every non-obvious design rule is recorded here as
**Decision / Rationale / Alternatives rejected**. Earlier decisions are cited as "001 R#"
([001 research.md](../001-match-data-ingestion/research.md)) and "002 R#"
([002 research.md](../002-data-contract/research.md)).

Evidence for this document was gathered on 2026-10-17. Nothing was added to the repository:
- **Dependency probe**: a scratch copy of `pyproject.toml` + `uv.lock` (uv 0.12.5), with dbt added
  to a `transform` group and the lock re-resolved; `uv sync` and the group flags were tried there.
- **dbt probe**: a scratch dbt project with the models, tests and configs this plan specifies,
  run with dbt-core 1.11.16 / dbt-postgres 1.11.0 against a throwaway `postgres:17.6-alpine`
  container (port 55433, never the project's database).
- **Real data**: the project's loaded database (237,473 matches, revision
  `0003_contract_constraints`, left by 002 T036) was read with `SELECT`s only, then copied with
  `pg_dump -n public` into the throwaway container. All builds, `EXPLAIN`s and index experiments
  ran on the copy.
- **Fixture probe**: the planned fixture `matches_transform.csv` (plan.md "Test Fixtures") was
  generated in the scratchpad, ingested with the project's own `football-stats ingest` into a
  fresh database migrated to head, and built with the probe project. Every mart value matched the
  expected values, which were computed separately by a plain-Python script that does not use SQL
  (30 team-form rows, 5 + 5 league-season rows, 9 rating rows: 0 differences).
- **Image probe**: a scratch `Dockerfile` with the planned `transform` stage, built and run against
  the copy.

---

## R1. dbt versions: dbt-core 1.11.x + dbt-postgres 1.11.x

**Facts (measured)**

| Item | Value |
|---|---|
| Latest releases on PyPI (2026-10-17) | dbt-core 1.12.6 (2026-10-14), 1.11.16 (2026-10-14), 1.11.15 (2026-09-08); 2.0.0rc9 and 1.13.0b1 are pre-releases; dbt-postgres 1.11.0 (2026-07-16) |
| dbt-core 1.12.x dependency | requires `dbt-core-experimental-parser>=2.0.0b1`, which is published **only as a 4.7 kB sdist**. Its build backend downloads a platform wheel from `github.com/dbt-labs/dbt/releases/...` at install time. In the probe that download failed (`CERTIFICATE_VERIFY_FAILED`), so `uv sync` failed. The downloaded wheel is not covered by any hash in `uv.lock` |
| dbt-core 1.11.x dependencies | ordinary wheels only (no experimental parser) |
| Resolution with `dbt-core>=1.11.15,<1.12`, `dbt-postgres>=1.11.0,<1.12` | succeeds against the existing lock: dbt-core 1.11.16, dbt-postgres 1.11.0, dbt-adapters 1.24.5, dbt-common 1.39.0, psycopg2-binary 2.9.13, agate 1.9.1; **35 new packages**; polars 1.44.2, pandera 0.34.1 and the dev tools are unchanged; no pandas, no numpy |
| Python | dbt-core 1.11 requires `>=3.10`; works on the project's 3.12 |
| `import dbt.cli.main` (cold) | 3.6 s first time, about 1.5 s warm |

**Decision**: Add `dbt-core>=1.11.15,<1.12` and `dbt-postgres>=1.11.0,<1.12` to a new
dependency group `transform` (R2). Exact versions come from `uv.lock` (expected: 1.11.16 / 1.11.0).
`tests/unit/test_transform_project.py::test_dbt_pinned_to_1_11` makes the upper bounds a gated
fact.

**Rationale**: 1.11 is the newest line whose whole dependency tree is wheels that the lock pins by
hash. A build therefore needs no network beyond the package index, and it is reproducible in CI,
in Docker and offline from the uv cache (constitution IV "pinned dependency versions"). The line is
still patched: 1.11.16 shipped on the same day as 1.12.6. The features this plan uses all exist in
1.11 and were exercised in the probe: `flags:` in `dbt_project.yml`, model contracts with
constraints, `indexes`, `data_tests:` with `arguments:`, and `run_results.json`.

**Alternatives rejected**:
- dbt-core 1.12.x: its install downloads an un-hashed binary from GitHub at build time. That fails
  behind TLS-intercepting proxies (it failed here) and escapes the lockfile.
- dbt-core 2.0 release candidates: pre-release, and a major version change.
- The official `ghcr.io/dbt-labs/dbt-postgres` image: its versions are pinned outside `uv.lock`,
  so CI tests and the container could run different dbt versions.
- `pip install dbt-postgres` in the Dockerfile: unpinned transitive dependencies.

## R2. Packaging: a `transform` dependency group, a default group for development, excluded from the app image

**Facts (measured with uv 0.12.5 in the scratch project)**
- With `[tool.uv] default-groups = ["dev", "transform"]`, `uv sync --frozen` installs dbt, so the
  existing CI step (`uv sync --frozen`) and `scripts/check.sh` (`uv run --frozen ...`) see it with
  no flag changes.
- `uv sync --frozen --no-dev --no-install-project` (the current app `Dockerfile` command)
  **installs dbt** once `transform` is a default group: 7 `dbt-*` packages. With
  `--no-default-groups` it installs none.
- `uv sync --frozen --only-group transform` installs only dbt and its dependencies: no project,
  no polars, no pandera.
- Venv sizes: app image `.venv` 212 MB. dbt-only `.venv` 96 MB. Images: app `football-stats:local`
  584 MB; probe transform image 430 MB.

**Decision**:
- `pyproject.toml`: new group `transform`; `[tool.uv] default-groups = ["dev", "transform"]`; dev
  group gains `types-PyYAML`, needed because tests parse `profiles.yml` and `dbt_project.yml`
  under `mypy --strict` (PyYAML itself already comes with dbt).
- `Dockerfile`: multi-stage with a shared `base` stage. Stage `transform` runs
  `uv sync --frozen --only-group transform`. Stage `app` (kept **last**, so plain `docker build .`
  and the existing `build: .` services still build it) changes `--no-dev` to `--no-default-groups`.
- `scripts/smoke_compose.sh` asserts that the app image cannot `import dbt` and that the transform
  image cannot `import football_stats`.

**Rationale**: The project code never imports dbt. dbt is a separate tool that reads the database,
so it belongs in its own group and image. Keeping it out of the app image saves about 100 MB and
about 35 packages there. Keeping polars/pandera out of the transform image keeps it small (430 MB
against about 680 MB for a combined image). One Dockerfile and one lockfile mean one set of pinned
versions for tests, CI and containers.

**Alternatives rejected**:
- dbt as a main dependency: it would enter the app image and every `football-stats` install for
  nothing.
- A separate `transform/pyproject.toml` with its own lock: two lockfiles to keep in step, and the
  tests (which run in the main venv) would use a different dbt than the container.
- A non-default group used with `--group transform` everywhere: every `uv run`/`uv sync` in
  check.sh, CI and README would need the flag, and an inexact `uv run` could silently drop it.

## R3. Project location and connection: `transform/`, profile from environment variables only

**Facts (measured)**: a profile whose fields are all `env_var(...)` works with `--profiles-dir`
pointing at the project. Run with `env -i` (only `PATH`, `HOME`), dbt read nothing else from the
environment and did not create `~/.dbt`. A missing `DBT_ENV_SECRET_PG_PASSWORD` exits 2 with
"Credentials in profile ... invalid". An unreachable port exits 2. A failing data test exits 1. The
`DBT_ENV_SECRET_` prefix makes dbt scrub the value from logs: the probe log printed `*****` where the
password appeared.

**Decision**:
- The dbt project lives in `transform/` (`dbt_project.yml`, `profiles.yml`, `models/staging`,
  `models/marts`, `macros/`, `tests/` with `tests/generic/`). The project name is
  `football_analytics`.
- `transform/profiles.yml` has one profile `football_analytics`, target `default`, `type: postgres`.
  Every connection field is an `env_var()` with **no default**: `DBT_PG_HOST`, `DBT_PG_PORT`
  (`| as_number`), `DBT_PG_USER`, `DBT_ENV_SECRET_PG_PASSWORD`, `DBT_PG_DBNAME`. It also sets
  `schema: analytics` and `threads: 4`. Nothing secret is committed.
- dbt is always invoked with `--project-dir transform --profiles-dir transform` (in the container,
  `/app/transform`). Run artifacts go to `DBT_TARGET_PATH` / `DBT_LOG_PATH`. The image sets these to
  `/tmp/dbt/...`, the tests set them to `tmp_path`, and a host run uses `transform/target` and
  `transform/logs`, which are git- and docker-ignored.
- One command:
  - in the stack: `docker compose run --rm transform`;
  - on the host, once the variables are exported (`.env.example` defines them from the
    `POSTGRES_*` values, and `set -a; . ./.env; set +a` exports them):
    `uv run --frozen dbt build --project-dir transform --profiles-dir transform`.
  - contracts/transform.md is the interface.

**Rationale**: FR-008 asks for configuration through environment variables only. dbt's own
`env_var()` does this with no wrapper code. Explicit, prefix-scrubbed names follow dbt's
secret-handling convention. Having no defaults means a misconfigured run fails loudly (exit 2), and
never silently connects to `localhost`. `transform/` names the layer by its role. It matches the
Compose service name and avoids a top-level `dbt/` directory, which is easy to confuse with the
`dbt` Python package in tracebacks and imports.

**Alternatives rejected**:
- A `football-stats transform` Python subcommand that parses `DATABASE_URL` and runs dbt: more
  code in `src/`. It would also need dbt in the app image, or a command that only works in one of
  the two images.
- Reading `POSTGRES_PASSWORD` directly in the profile: the value would not be scrubbed from dbt's
  logs.
- `~/.dbt/profiles.yml`: per-machine state outside the repository; the container and the tests
  could not share it.
- The location `dbt/` (PLAN.md's suggested layout): see the rationale above.

## R4. Schemas: `analytics_staging` (views) and `analytics_marts` (tables), nothing in `public`

**Facts (measured)**: with profile `schema: analytics` and `+schema: staging` / `+schema: marts`,
dbt's default `generate_schema_name` creates exactly `analytics_staging` and `analytics_marts`. No
`analytics` schema is created, because no node targets it. `public` is not touched: in the probe,
the catalog and the data of `public` were the same before and after a build. Tables are built as
`<name>__dbt_tmp`, then swapped in by rename inside one transaction, so readers never see a
half-built table.

**Decision**: staging models are views in `analytics_staging`; marts are tables in
`analytics_marts`. No custom `generate_schema_name` macro. No `on-run-start`/`on-run-end` hooks and
no `pre-hook`/`post-hook` anywhere. The models only read `public` through `source()`.
`test_manifest_has_no_hooks_and_only_analytics_schemas` checks this, and
`test_public_tables_unchanged_by_build` compares a snapshot of `public` taken before and after the
build (FR-007).

**Rationale**: separate schemas keep ingestion (owned by Alembic) and analytics (owned by dbt)
apart. A `DROP SCHEMA analytics_marts CASCADE` can never touch an ingested row. The spec's
assumption (staging as views, marts as tables) fixes the materializations.

**Consequence (documented, accepted)**: the staging views depend on `public.leagues`, `teams` and
`matches`. A future Alembic revision that drops or retypes a column a view selects, and
`alembic downgrade base`, will fail with "other objects depend on it" until the analytics schemas
are dropped. Ingestion is not affected (it only runs DML). The README records this. A future
revision that needs it ships `DROP SCHEMA IF EXISTS analytics_staging CASCADE` (the next `dbt build`
recreates it). No current migration is affected; the integration tests build on their own databases.

**Alternatives rejected**: marts in `public` (mixes owners, risks name collisions with ingestion
tables); one `analytics` schema (staging and marts could not be told apart, or dropped separately);
staging as tables (contradicts the spec's assumption and doubles storage for no benefit).

## R5. How the gate runs dbt: subprocess, one session-scoped build, rolled-back mutation checks

**Facts (measured)**
- `dbt build` as a subprocess on a fixture database takes **3.2 s** wall time (including interpreter
  start and parse); on the full real copy, 4.4–4.7 s.
- `target/run_results.json` lists every node with `unique_id`, `status` (`success`/`pass`/`fail`/
  `error`/`skipped`), `failures` and `compiled_code`. For a failing data test, the console prints
  `FAIL <n> <test_name>` and `Done. PASS=… ERROR=1`, and the exit code is 1.
- The `compiled_code` of a singular test is plain SQL with fully qualified names
  (`"<db>"."analytics_marts"."mart_…"`). Run on the same database inside `BEGIN … ROLLBACK` after a
  corrupting `UPDATE`, it returns the offending rows. Nothing persists.
- The existing gate takes 24 s (184 unit + 121 integration tests).

**Decision**:
- Tests run dbt **as a subprocess**: `[str(Path(sys.executable).with_name("dbt")), *args,
  "--project-dir", <dir>, "--profiles-dir", <dir>]`. The environment is built from scratch:
  `PATH`, `HOME=<tmp>`, the five connection variables parsed from the testcontainers URL,
  `DBT_TARGET_PATH`, `DBT_LOG_PATH`. The test reads the exit code, stdout and `run_results.json`.
  Helper: `tests/integration/dbt_runner.py` (data-model.md §5).
- One **session-scoped** fixture `transformed_db` clones the migrated template, ingests
  `matches_transform.csv` with `pipeline.run(..., started_at=CONTRACT_STARTED_AT)`, snapshots
  `public`, runs `dbt build` once, snapshots `public` again, and yields the URL, the result and both
  snapshots. Every read-only test uses it.
- Each singular test is proven to work by a **mutation case**. A corruption runs inside a
  transaction on `transformed_db`; the test then runs that test's `compiled_code` (taken from the
  session build's `run_results.json`) in the same transaction, asserts that at least one row comes
  back, and rolls back. No extra dbt run is needed, and the shared database is unchanged afterwards.
- Separate dbt runs only where a test needs its own state: idempotency (2 builds), empty database
  (1), real sample (1), failing build followed by a recovery build on the same database (2, R18),
  missing credentials (1, no database needed). Together with the session build that makes **8 dbt
  invocations** in the suite. The expected added time is about 28 s, so the gate should stay under
  60 s. T037 measures it.

**Rationale**: a subprocess runs exactly what the container and the README run. Its environment is
explicit (no leakage from the developer's shell), its global state is isolated, and its exit code is
asserted directly (US1-AS3). `dbtRunner` in-process would share dbt's logging configuration and
`os.environ` with pytest (the profile reads `os.environ`, so tests would have to mutate it). It
would also make mypy type-check dbt's internals. The mutation technique runs each SQL invariant
twice: once on correct data (must pass, via the session build) and once on corrupted data (must
fail). This proves the tests are not vacuous, at the cost of 9 rolled-back transactions instead of 9 builds.

**Alternatives rejected**:
- One fresh build per test: about 3 s each, so 20+ builds is over a minute.
- Cloning the built database with `CREATE DATABASE … TEMPLATE` for mutation cases: the template must
  have no open connections, and the compiled SQL names the original database, so it would have to
  be rewritten.
- dbt unit tests (`unit_tests:` YAML): they test a model against hand-written inputs, but bypass
  ingestion. FR-009 wants marts checked on data that went through the real pipeline and types. That
  would also be a second place for expected values.
- `dbt-utils` / other packages for `unique_combination_of_columns` and the like: needs
  `dbt deps` (network) or vendored third-party code. One 6-line project-local generic test does the
  job (R10).

## R6. Determinism and no network

**Decision**:
- `dbt_project.yml` `flags:` `send_anonymous_usage_stats: false` (dbt's tracker would otherwise call
  out to Snowplow), `partial_parse: false` (every run parses from source, so a stale
  `partial_parse.msgpack` can never change behaviour), `use_colors: false`.
- No `packages.yml`/`dependencies.yml`, so `dbt deps` is never needed. Tests never run `dbt --version`
  (it checks PyPI) or `dbt deps`.
- `tests/unit/test_transform_project.py::test_project_is_offline_and_deterministic` asserts the
  three flags and that neither package file exists.
- Every model orders its windows by a total order (R8, R9). No model uses `now()`, `current_date`,
  `random()` or sequences. `test_models_use_no_volatile_functions` greps the model and test SQL for
  them.
- Fixture ingests pass `started_at=CONTRACT_STARTED_AT` (002 rule). The fixture's latest date
  (2022-08-06) is before it, so 002's `test_run_date_after_all_fixture_dates` stays green.

**Rationale**: the gate must not depend on the network, the clock or the order of runs (planner
rules). Each of these is a concrete source of nondeterminism we observed or can name.

## R7. Season label (FR-003)

**Facts (real data, read-only)**: 38 leagues. In every league, between 44.8 % and 62.9 % of matches
are dated July–December. That includes the calendar-year leagues (ARG, BRA, USA, JAP, NOR, SWE,
…), whose seasons run January–December, so the July rule splits each of their calendar seasons
across two labels. This is accepted by the spec's assumptions. The earliest match is 2000-07-28, but
the date contract allows 2000-01-01, which would get label `1999/00`.

**Decision**: computed once, in `stg_matches`:
`season_start_year = extract(year) if extract(month) >= 7 else extract(year) - 1` (integer);
`season = season_start_year || '/' || lpad(((season_start_year + 1) % 100)::text, 2, '0')`, for
example `2009/10` and `1999/00`. Both columns are carried into the marts, so ordering never sorts
text. The rule is documented in contracts/marts.md and the README.
`test_season_label_rule` covers the boundaries.

**Rationale**: FR-003 fixes the rule. The modulo plus `lpad` gives the century wrap (`1999/00`)
without special cases. Computing it once in staging gives one definition for all marts.

**Alternatives rejected**: a per-league season calendar (out of scope by the spec); a `date_trunc`
on fiscal years (no such Postgres function); a lookup table of seasons (more data to maintain for
the same rule).

## R8. Team-match unpivot and rolling form (FR-005)

**Facts (real data)**: no team plays twice on the same date (0 pairs), and `kickoff_time` is null in
54.8 % of matches.

**Decision**:
- The team-match unpivot is a CTE inside `mart_team_form`: `UNION ALL` of the home perspective and
  the away perspective of `stg_matches`. There is no separate model; the mart *is* the team-match
  grain plus the form columns.
- Window: `WINDOW prev5 AS (PARTITION BY team_id ORDER BY match_date, match_id ROWS BETWEEN 5
  PRECEDING AND 1 PRECEDING)`. `form_matches = count(*) OVER prev5` (0–5);
  `form_points/form_goals_for/form_goals_against = sum(...) OVER prev5`. These are **null** when
  `form_matches = 0`, because Postgres `sum` over an empty frame is null; this is the spec's "empty
  for its first match". `team_match_number = row_number() OVER (PARTITION BY team_id ORDER BY
  match_date, match_id)`.
- Tie-breaker: `match_id`, which is unique, so the order is total. It is stable across re-ingests,
  because upserts keep ids (001 R11). On a fresh load ids follow source order (`INSERT … ORDER BY
  s.source_row_number`), so for two matches of one team on the same date, the earlier source row
  counts as the earlier match. "Strictly earlier" means earlier in `(match_date, match_id)` order.

**Rationale**: `ROWS` (not `RANGE`) with `1 PRECEDING` as the end excludes the current row by
construction. The 5-row start caps the window. A total order makes the frame deterministic even in
the same-date case, which the fixture includes on purpose (rows 4 and 5, plan.md). A CTE avoids a
model that nothing else uses (YAGNI).

**Alternatives rejected**:
- Tie-breaker `kickoff_time`: null in more than half of the data.
- Tie-breaker `(league_code, home_team_id)`: total, but arbitrary and harder to explain than
  "load order".
- `RANGE BETWEEN '…' PRECEDING`: a date-interval frame, not "previous 5 matches".
- A separate `int_team_matches` model: dbt cannot test an ephemeral model, and a view adds a third
  schema for one consumer.
- Correlated subqueries with `LIMIT 5`: O(n²) without an index; the window is one sort.

## R9. Team ratings: latest, peak and lowest Elo with dates and tie rules

**Facts (real data)**: 1,221 teams; 438 have no Elo in any match; 783 are rated. In 463 teams the
peak value occurs on more than one date, and in 491 the lowest value does, so the tie rule matters.
Elo ranges from 1103.13 to 2107.48 (`double precision`). No team has two rated matches on one date.

**Decision**: team perspective of `stg_matches`, keeping only rows where the team's pre-match Elo
is not null:
- `latest_*` = the row first by `match_date DESC, match_id DESC`;
- `peak_*` = first by `elo DESC, match_date ASC, match_id ASC`, so ties go to the **earliest** date;
- `lowest_*` = first by `elo ASC, match_date ASC, match_id ASC`, so ties also go to the **earliest**
  date.
These use `row_number()` per team (three windows), then a filtered aggregate. Every team in
`stg_teams` gets a row (`LEFT JOIN`). Teams with no rated match have `rated_matches = 0` and all six
value/date columns null. Matches with a null Elo still count everywhere else (they are in team form
and the league marts).

**Rationale**: "earliest date" for peak and lowest answers "when did the team first reach it". This
is the user-specified rule and is deterministic. `latest` must be the newest by definition. Keeping
unrated teams as rows means every team id has exactly one rating row, which a `relationships` test
can check from both sides. The spec's "no rating values" becomes nulls, not a missing row.

**Alternatives rejected**: `DISTINCT ON` (Postgres-specific shorthand; the singular test uses it as
the *independent* formulation, R10); `array_agg(... ORDER BY ...)[1]` (works, but hides the tie
rule); computing our own Elo (out of scope).

## R10. Data tests (FR-006): generic, project-local generic, singular

**Decision**:
- Generic tests (dbt built-ins), declared in `models/staging/_staging.yml` and
  `models/marts/_marts.yml` with the dbt 1.11 `arguments:` syntax (no deprecation warnings in the
  probe): `unique` + `not_null` on every single-column key; `relationships` on every foreign key
  (`stg_matches.league_code → stg_leagues`, `home_team_id/away_team_id → stg_teams`,
  `mart_team_form.match_id → stg_matches`, `team_id/opponent_team_id → stg_teams`, league marts'
  `league_code → stg_leagues`, `mart_team_ratings.team_id → stg_teams`); `accepted_values` on
  results (`H/D/A`, `W/D/L`), points (`0,1,3`) and `form_matches` (`0..5`); `not_null` on every
  mart measure that must be present.
- One project-local generic test `transform/tests/generic/unique_combination_of_columns.sql`, for
  the composite keys `(match_id, team_id)` and `(league_code, season)`.
- Singular tests in `transform/tests/` (data-model.md §4), each with an independent
  reformulation:
  `assert_team_form_two_rows_per_match`,
  `assert_team_form_matches_lag_recomputation` (LAG 1–5 instead of a frame, so a frame that
  included the current or a later row would disagree),
  `assert_home_advantage_shares_sum_to_one`,
  `assert_home_advantage_reconciles_with_staging`,
  `assert_goals_reconcile_with_staging`,
  `assert_team_ratings_reconcile_with_staging` (`DISTINCT ON` formulation of the tie rules, plus the
  "unrated ⇒ all null" rule).
  The two `reconcile` tests use `FULL OUTER JOIN … IS DISTINCT FROM`, so a missing or extra
  league-season fails too.
- Every singular test has a mutation case (R5). `test_singular_tests_cover_every_file` checks that
  the set of case ids equals the set of `transform/tests/*.sql` names.

**Rationale**: FR-006 names exactly these categories. Each invariant is written differently from the
model it checks: lag versus frame, `DISTINCT ON` versus `row_number`, re-aggregation from staging.
Without that, a test would reproduce the same bug. The final probe (all contracts and all
tests in this plan) built and tested 71 nodes (7 models, 64 tests) with 0 failures on the full real
copy (3.4 s of dbt time), on the fixture and on an empty database. Every mutation case of
data-model.md §4 made its singular test return rows (1, 14, 5, 1, 5 and 1 rows); on unmodified data
each returned 0.

**Alternatives rejected**: `dbt_utils`/`dbt_expectations` (network or vendoring, R5); asserting
invariants only in pytest (they would not run in the container on real data, SC-003); a unique test
on a concatenated text expression (works, but obscures the key).

## R11. Mart shape: dbt model contracts with primary keys

**Facts (measured)**: with `contract: {enforced: true}` and declared `data_type`s, dbt creates the
table with exactly those column types and constraints. It fails the model before creating anything
if the query's column types differ in kind: the probe declared `integer` for a numeric column and got
"data type mismatch". The precision of `numeric(5,4)` comes from the DDL. Primary keys become real
constraints. Their auto-generated names alternate between builds (`…__dbt_tmp_pkey`,
`…__dbt_tmp_pkey1`) because of the tmp-table swap.

**Decision**: all four marts have `contract: {enforced: true}`, every column with its `data_type`
(data-model.md §3), `not_null` constraints on keys, and a `primary_key` constraint:
`mart_team_form (match_id, team_id)`, both league marts `(league_code, season)`,
`mart_team_ratings (team_id)`. Staging views have no contracts (internal).
`test_mart_columns_match_contract` compares `information_schema.columns` with data-model.md §3, so
the documented types are gated. Catalog comparisons in idempotency tests ignore index and constraint
**names** (R12).

**Rationale**: the marts are the interface for the Phase 5 API (contracts/marts.md). A contract turns
schema drift into a failed build, in the gate and in the container, rather than a broken consumer.
Primary keys make a duplicate key fail at write time, as well as through the data tests that
FR-006 requires.

**Alternatives rejected**: no contract, with types asserted only in pytest (drift on real data would
go unnoticed in the container); contracts on staging views (views cannot hold constraints; low
value).

## R12. Idempotency and "ingestion tables untouched" (FR-007, SC-004)

**Decision**:
- **Identical contents**: `helpers.analytics_snapshot(conn)` returns, for each of the 7 models,
  `SELECT to_jsonb(t) FROM <schema>.<model> t ORDER BY <key>` (keys in data-model.md §2–3). For the
  views this is their current output. `test_second_build_is_identical` builds twice on its own
  database and compares the snapshots, plus `analytics_catalog_snapshot(conn)` (columns and types,
  and index/constraint definitions with the generated names stripped by regex), plus `public`
  (`helpers.snapshot` and `helpers.catalog_snapshot`) before, between and after.
- **Untouched**: the session fixture captures `helpers.snapshot` + `helpers.catalog_snapshot`
  (schema `public`) plus `alembic_version` contents before and after its build;
  `test_public_tables_unchanged_by_build` asserts
  they are equal. A manifest check (R4) shows that no model, hook or operation targets `public`.

**Rationale**: dbt's table materialization fully replaces each mart from the same deterministic
query, so contents are the same; the test proves it, including for numerics and nulls. The two
public snapshots reuse 001/002 helpers, so "untouched" means what it meant there: every row and
every catalog entry of `public`. Index names are excluded because dbt-postgres builds them from an
MD5 hash that includes the current timestamp (code read in `dbt/adapters/postgres/impl.py`), and the
primary-key names alternate (R11). Neither is content.

**Alternatives rejected**: comparing `pg_dump` output (includes those names; needs `pg_dump` in the
test environment); running the transform as a read-only role (roles are cluster-level and can't be
created by an Alembic migration cleanly; the snapshot proves the property for the actual
statements).

## R13. Shares, averages and numeric types

**Facts (real data)**: 179 of 789 league-seasons have shares whose rounded sum is not exactly 1
(for example `0.3333 × 3 = 0.9999`).

**Decision**: shares are `round(count::numeric / matches_played, 4)::numeric(5,4)`; averages and
goals per match are `round(sum::numeric / matches_played, 4)::numeric(6,4)`. Postgres rounds
`numeric` half away from zero, and the fixture expectations use the same rounding (Python
`ROUND_HALF_UP` on `Decimal`). Counts stay `bigint` (`count(*)`, `sum(integer)`). Invariant
tolerance: `abs(home_win_share + draw_share + away_win_share - 1) <= 0.00015`, the maximum
rounding error of three values rounded to 4 places. The fixture has a league-season (SP1 2021/22)
whose sum is exactly `0.9999`, which proves the tolerance is needed. Zero counts give `0.0000`, never
null, because `matches_played >= 1` by construction (a group exists only if it has matches).

**Rationale**: exact decimal types make the fixture comparison exact (string compare of
`numeric::text`). Four places are enough to tell leagues apart and are readable for the API; the raw
counts remain for exact math.

**Alternatives rejected**: `double precision` (inexact equality in tests); unrounded `numeric` (more
than 20 digits, a tolerance would still be needed, and unreadable); percentages 0–100 (the spec's
100 % is presentation).

## R14. Indexes (FR-010, SC-005): measured, only where they help

**Facts (measured on the full real copy, PostgreSQL 17.6, warm cache, `EXPLAIN (ANALYZE,
BUFFERS)`)**

| Candidate | Query it would serve | Before | After | Verdict |
|---|---|---|---|---|
| `mart_team_form (team_id, match_date, match_id)` (dbt `indexes` config) | team form lookup: `WHERE team_id = $1 ORDER BY match_date DESC, match_id DESC LIMIT 10` (Phase 5 `GET /teams/{id}/form`) | 7.5–10.1 ms, Parallel Seq Scan + top-N sort, 7,400 buffers | 0.020–0.027 ms, Index Scan Backward, 4 buffers | **add**. The planner used it right after a build, before any `ANALYZE`; it adds about 0.1 s to the build |
| `public.matches (match_date)` (would be Alembic 0004) | the source scan of every staging/mart model | about 52–71 ms Parallel Seq Scan with or without the index (the difference is cache noise; plan unchanged) | unchanged | **reject**: full rebuilds scan everything |
| same | cross-league date range (`WHERE match_date BETWEEN …`, no model issues it) | 9.3 ms (Index Only Scan over the whole of `matches_natural_key`, condition on a non-leading column, 913 buffers) | 0.05 ms | **reject** for this feature: no model or planned consumer runs it (YAGNI); recorded in the README as a measured negative result |
| `public.matches (league_code, match_date)` | league + date range | 0.12 ms already, Index Only Scan on `matches_natural_key (league_code, match_date, home_team_id, away_team_id)` | n/a | **reject**: redundant with the natural key |
| league-season marts / ratings primary keys (R11) | `WHERE league_code = $1 AND season = $2`; `WHERE team_id = $1` | 789 and 1,221 rows | 0.013 ms with the primary key | kept for integrity, not speed; measured and listed in the README so every index has a number |

**Decision**: no Alembic migration in this feature (head stays `0003_contract_constraints`). The only
index added for a query is the `mart_team_form` lookup index, declared in the model's `config(
indexes=[{"columns": ["team_id", "match_date", "match_id"]}])`. The primary keys come from the
contracts. `scripts/explain_transforms.sql` holds the measurement procedure (data-model.md §6). It
creates each candidate inside `BEGIN … ROLLBACK`, and drops the mart index or key inside
`BEGIN … ROLLBACK` for the "before" plan, so no measurement leaves a manual schema change. T036
runs it on the full dataset and records the before/after and the negative results in the README.
If T036 finds that a rejected candidate speeds up a staging/mart source query by more than 20 %, it
stops and records a plan deviation in `decisions.md` instead of adding an unplanned migration.

**Rationale**: FR-010 and SC-005 require that every index be versioned and measured. "Only add an
index if it measurably helps" means the default is no index. Mart tables are dropped and recreated
by every build, so an Alembic migration cannot own their indexes. The dbt model config is their
versioned definition, applied by every build and never by hand, which is what US3-AS2 asks for.
Indexes on `public` remain Alembic-only (constitution).

**Alternatives rejected**: Alembic 0004 with `matches(match_date)` and
`matches(league_code, match_date)`: measured, does not help the transform, and the second one
duplicates the natural key. `post-hook` `CREATE INDEX` with a fixed name: deterministic name, but
it is a hook (R4 forbids hooks so "nothing writes outside analytics" stays easy to check), and dbt's
`indexes` is the documented mechanism.

## R15. Compose service, smoke test and CI (FR-008, US4)

**Decision**:
- `docker-compose.yml` gains service `transform`: `image: football-stats-transform:local`,
  `build: {context: ., target: transform}`, environment `DBT_PG_HOST: db`, `DBT_PG_PORT: "5432"`,
  `DBT_PG_USER: ${POSTGRES_USER}`, `DBT_ENV_SECRET_PG_PASSWORD: ${POSTGRES_PASSWORD}`,
  `DBT_PG_DBNAME: ${POSTGRES_DB}`, `depends_on: migrate: service_completed_successfully`,
  `profiles: ["transform"]`, `restart: "no"`. The image's `CMD` is the `dbt build` command (R3). It
  runs as uid 10001; artifacts go to `/tmp/dbt`.
- It does **not** depend on `ingest`: `depends_on` would make every `docker compose run transform`
  start an ingest, which re-downloads 45 MB. The documented order is
  `docker compose run --rm ingest && docker compose run --rm transform`. Scheduling one after the
  other is Phase 4 (spec assumption). Running the transform on a database with no ingest builds
  empty marts and exits 0 (spec edge case).
- `scripts/smoke_compose.sh`: build both profiles; after the fixture ingest, run `transform` twice.
  Each run must exit 0 with `mart_team_form` = 24 rows, `mart_league_season_goals` = 2,
  `mart_team_ratings` = 6, `mart_league_season_home_advantage` = 2 (values from the probe on
  `matches_valid.csv`). It also checks the image separation (R2).
- CI: unchanged steps plus `docker build --target transform -t football-stats-transform:ci .`
  after the existing image build. The transform tests run inside `scripts/check.sh` (US4-AS2).

**Rationale**: a profile keeps the transform an explicit job, as `ingest` is (001 R16). Sharing the
Dockerfile and lock keeps the versions identical. The smoke test is the only automated proof that
the container (not just the Python venv) works.

**Alternatives rejected**: `depends_on: ingest` (see above); running dbt inside the app image (R2);
a long-running service (`docker compose up` would rebuild the marts on every start).

## R16. Concurrency with ingestion (known limitation)

**Decision**: documented, not engineered. Each dbt model runs in its own transaction. If an ingest
commits in the middle of a build, two marts can see different snapshots, and the `reconcile` tests
would then fail the build. That is loud, not silent. The README says: do not run `transform` while
`ingest` is running. Phase 4 orchestration will serialize them.

**Alternatives rejected**: an `on-run-start` advisory lock (session-level, held by one of dbt's
pooled connections, so it does not protect the others; and R4 forbids hooks); one transaction for
the whole build (dbt does not offer it for multiple models).

## R17. Planning profile of the real dataset (for quickstart and README)

From the probe build of the planned models on the full copy (to be confirmed by T036, not copied):
league-seasons 789; `mart_team_form` 474,946 rows (= 2 × 237,473); `mart_team_ratings` 1,221 rows,
783 rated; `E0 2015/16` 380 matches with shares `0.4132 / 0.2816 / 0.3053`; final probe build: 71 nodes
(7 models, 64 tests), `PASS=71`, dbt time 3.37 s (the earlier, smaller probe: 4.7 s wall time for
36 nodes). The planned project has 4 more staging tests than the
probe (`ft_result`, `away_result`, `away_points`, `season_start_year`), so the expected final count
is **75 nodes (7 models + 68 tests)**, as data-model.md §2–§4 specify. SC-001's target is under
2 min. If T036 measures different numbers,
it reports the difference and does not edit the expectation.

## R18. State after a failed build, and retry (constitution II "safe to retry", plan review P1-1)

**Facts**: in `dbt build`, each model's tests run *after* that model has been created. A table is
swapped in atomically, then tested. When a test fails, dbt marks every node downstream of it
`skipped` and exits 1. A model's own failure (SQL error, contract mismatch) leaves the previous
table in place, because the swap is rolled back with the model's transaction.

**Decision (documented, tested; no swap machinery)**:
- **What exit 1 leaves behind**: every model built before the failing node holds the new contents,
  including a mart whose own singular test failed: it stays published. Every skipped downstream
  model keeps its previous contents, or does not exist on a first build. `analytics_marts` can then
  mix tables from two runs. **Contract**: after a non-zero transform exit, consumers must treat the
  analytics schemas as not trustworthy until the next exit-0 run (contracts/transform.md, README).
  `public` is never affected. Phase 4 orchestration will gate consumers on the exit code.
- **Retry**: every model is a full rebuild from `public` (no incremental state, no dependence on
  previous analytics contents). A rerun on the same database therefore converges to exactly what a
  fresh build produces. `test_build_fails_and_names_failing_test` proves it on one database:
  1. failing build: exit 1, failing test named, downstream marts `skipped`;
  2. unmodified rerun: exit 0;
  3. `analytics_snapshot` equals the session build's, and `public` is unchanged.

**Rationale**: the constitution requires that a run be *safe to retry after a partial failure*.
It does not require atomic publication of all models. Retry safety holds by construction (full
rebuilds from an untouched source) and is now shown on the same database. The only consumer of the
marts before Phase 4 is the developer, who sees the exit code. The simplest honest stance is to
state the intermediate state and make the rerun the documented recovery.

**Alternatives rejected**:
- `dbt build --fail-fast`: stops at the first failure, but the mart whose test failed is already
  published, and more marts stay stale. It changes how much is stale, not whether the schema can be
  trusted.
- Build into a temporary schema and swap schemas only when every test passes (blue/green): needs
  custom `generate_schema_name` logic plus `ALTER SCHEMA … RENAME` operations run from
  `run-operation` or hooks outside dbt's materializations. That is new DDL code paths (R4 forbids
  hooks), and with two renames there is a window in which neither schema exists. Phase 4 can gate
  consumers more simply.
- `dbt run` then `dbt test` with a manual "publish" step: the same problem as blue/green, plus two
  commands instead of one (FR-008).
