---

description: "Task list for 003-dbt-transforms"
---

# Tasks: Analytics Transforms

**Input**: Design documents from `/specs/003-dbt-transforms/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/transform.md,
contracts/marts.md, quickstart.md

**Tests**: Required (constitution Principle III, planner rules). Every test task names the exact
test functions from plan.md "FR / SC → Test Mapping" and comes **before** the implementation task
that makes it pass. A test task is done when its tests exist, are collected, and fail for the
expected reason (missing file, missing relation, assertion). An implementation task is done when its
paired tests pass and `scripts/check.sh` exits 0.

**Existing codebase**: Features 001 and 002 are implemented and green (gate 24 s). `scripts/check.sh`
already exists and is **not modified**. No change to `src/football_stats/` or `migrations/`.

**Organization**: Setup → Foundational (fixture, test harness) → US1 (P1, staging + build
mechanics, MVP) → US2 (P1, marts) → US3 (P2, index + measurement procedure) → US4 (P2, container,
smoke, CI) → Polish (README, real-data run).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4 (user story phases only)

## Global rules for every task

- Python (tests only): full type hints (`mypy --strict`), google-style docstrings on public
  helpers, `logging` only, no bare or blind `except`.
- dbt SQL: lowercase keywords, CTEs, one model per file; read only through `source('football', …)`
  and `ref(…)`. No `now()`, `current_date`, `current_timestamp`, `random()`, `nextval`; no hooks; no
  `packages.yml`/`dependencies.yml`. Every window and every "first row" ordering ends with
  `match_id` (research.md R8, R9). Column names, types, keys and derivations are exactly
  data-model.md §2–§3; the singular tests and mutation SQL are exactly data-model.md §4.
- Generic test YAML uses the dbt 1.11 `data_tests:` + `arguments:` syntax. A build must print no
  deprecation warnings (`grep -ci deprecat` on the log is 0).
- Tests: no external network; no timestamp assertions; no reliance on test order. dbt runs only
  through `tests/integration/dbt_runner.run_dbt` with an environment from `dbt_env` and target and
  log dirs under `tmp_path`/`tmp_path_factory`. Never call `dbt deps` or `dbt --version`. Fixture
  ingests pass `started_at=tests.stages.CONTRACT_STARTED_AT`. Tests that only read use the session
  fixture `transformed_db`; tests that build get their own database (clone of `template_db` or
  `empty_db_url`).

---

## Phase 1: Setup

**Purpose**: dbt dependency and project skeleton; the gate stays green.

- [x] T001 Add dbt: run `uv add --group transform "dbt-core>=1.11.15,<1.12" "dbt-postgres>=1.11.0,<1.12"` and `uv add --dev types-PyYAML`, then add `[tool.uv]` `default-groups = ["dev", "transform"]` to `pyproject.toml` and run `uv lock`. Confirm that `uv.lock` resolves `dbt-core 1.11.16` (or a later 1.11.x), `dbt-postgres 1.11.0`, has no `dbt-core-experimental-parser`, `pandas` or `numpy` (`grep -E '^name = "(dbt-core-experimental-parser|pandas|numpy)"' uv.lock` prints nothing), and keeps `polars 1.44.2` and `pandera 0.34.1`. `uv sync --frozen` succeeds and `.venv/bin/dbt` exists. Run `scripts/check.sh`; it exits 0
- [x] T002 [P] Write `tests/unit/test_transform_project.py` (module docstring; reads files with `pathlib`, `tomllib`, `yaml.safe_load`; no subprocess): `test_dbt_pinned_to_1_11` (parse the `transform` group requirements with `packaging.requirements.Requirement`: both `dbt-core` and `dbt-postgres` are present, and each specifier set excludes `1.12.0` and admits some `1.11.x` (`"1.12.0" not in spec`, `"1.11.99" in spec`); `"transform"` and `"dev"` are in `tool.uv.default-groups`), `test_profile_reads_only_env_vars` (`transform/profiles.yml`: profile `football_analytics`, target `default`, output `type: postgres`, `schema: analytics`; `host`, `port`, `user`, `password`, `dbname` are each exactly one `{{ env_var('<NAME>') }}` call with no default, with names `DBT_PG_HOST`, `DBT_PG_PORT` (followed by `\| as_number`), `DBT_PG_USER`, `DBT_ENV_SECRET_PG_PASSWORD`, `DBT_PG_DBNAME`; no other key contains a literal credential), `test_project_is_offline_and_deterministic` (`transform/dbt_project.yml` `flags` has `send_anonymous_usage_stats: false`, `partial_parse: false`, `use_colors: false`; `profile: football_analytics`; no `on-run-start`/`on-run-end` keys; `transform/packages.yml` and `transform/dependencies.yml` do not exist), `test_models_layered_staging_and_marts` (`models.football_analytics.staging` has `+materialized: view`, `+schema: staging`; `marts` has `+materialized: table`, `+schema: marts`; every `transform/models/**/*.sql` is in `models/staging/` with prefix `stg_` or in `models/marts/` with prefix `mart_`), `test_models_use_no_volatile_functions` (no file under `transform/models` or `transform/tests` matches, case-insensitive, `\b(now|random|nextval|clock_timestamp|statement_timestamp)\s*\(|\bcurrent_(date|timestamp|time)\b|pre[-_]hook|post[-_]hook`; and no `.sql` file directly under `transform/tests/` contains `;`). Expected failure: files missing
- [x] T003 Create the dbt skeleton per research.md R3, R4, R6: `transform/dbt_project.yml` (`name: football_analytics`, `version: "1.0.0"`, `config-version: 2`, `profile: football_analytics`, `model-paths: ["models"]`, `test-paths: ["tests"]`, `macro-paths: ["macros"]`, the three `flags`, folder configs for `staging`/`marts`), `transform/profiles.yml` (as T002, `threads: 4`), `transform/models/staging/_sources.yml` (source `football`, `schema: public`, tables `leagues`, `teams`, `matches`), empty `transform/models/marts/`, `transform/tests/generic/` and `transform/macros/` each with a `.gitkeep`; append `transform/target/` and `transform/logs/` to `.gitignore` and `.dockerignore` (makes T002 pass)
- [x] T004 Run `scripts/check.sh`; it exits 0

**Checkpoint**: dbt installed and locked; empty project parses (not yet built).

---

## Phase 2: Foundational (fixture and test harness)

**Purpose**: shared fixture, dbt runner and session build used by every story. No dbt models yet.

- [x] T005 [P] Create `tests/fixtures/matches_transform.csv` (real 48-column header, 16 data rows exactly as in plan.md "Test Fixtures": base row B = data row 1 of `matches_valid.csv` with `HTHome`/`HTAway`/`HTResult` empty and the per-row overrides) and `tests/fixtures/matches_transform.expected.json` (five keys `stg_matches`, `mart_team_form`, `mart_league_season_home_advantage`, `mart_league_season_goals`, `mart_team_ratings`, each a list of objects with exactly the keys and order of plan.md; numerics as strings like `"0.4286"`, Elo as numbers, dates ISO, `null` for missing; the plan table's `home` column maps `H` → `"is_home": true`, `A` → `false`, a JSON boolean, never a string). Add a paragraph to `tests/fixtures/SOURCES.txt`: synthetic; covers the season boundary, a calendar-year league, a same-date double match, a 5-match window cap, missing and tied Elo, a three-outcome league-season, and one contract-violating row that must never reach a mart; the expected values were computed from the row table by a separate plain-Python script (no SQL), using `Decimal` with `ROUND_HALF_UP`, and were cross-checked independently by the plan reviewer (round 1). Check that `tests/unit/test_data_contract.py::test_run_date_after_all_fixture_dates` still passes
- [x] T006 [P] Write `tests/unit/test_dbt_runner.py::test_dbt_env_parses_url_and_passes_nothing_else`: `dbt_env("postgresql://us%40er:p%3Ass@db.example:6543/name", home=tmp, target_dir=tmp)` returns exactly the keys `PATH`, `HOME`, `DBT_PG_HOST` (`db.example`), `DBT_PG_PORT` (`6543`), `DBT_PG_USER` (`us@er`), `DBT_ENV_SECRET_PG_PASSWORD` (`p:ss`), `DBT_PG_DBNAME` (`name`), `DBT_TARGET_PATH` (`<tmp>/target`), `DBT_LOG_PATH` (`<tmp>/logs`), `DBT_SEND_ANONYMOUS_USAGE_STATS` (`false`), even when `monkeypatch.setenv("DBT_PROFILES_DIR", "/elsewhere")` and `PGPASSWORD` are set. Pure: no socket, no subprocess. Expected failure: ImportError
- [x] T007 Create `tests/integration/dbt_runner.py` per data-model.md §5 (`TRANSFORM_DIR`, `DbtResult` with `statuses`, `compiled_sql`, `manifest`, `dbt_env`, `run_dbt`; `run_results.json` read from `<target_dir>/target/run_results.json`, `[]` if missing; the logged command never contains the password; `cwd` = the parent of `DBT_TARGET_PATH`, never the repository) (makes T006 pass)
- [x] T008 Extend `tests/integration/helpers.py` (`ANALYTICS_MODELS`, `ANALYTICS_KEYS`, `analytics_snapshot`, `analytics_catalog_snapshot`, `mart_rows` per data-model.md §5; `analytics_snapshot(conn, models=tuple(ANALYTICS_MODELS))` takes the fixed model list; US1 tests pass the 3 staging names, later tests use the default 7) and `tests/integration/conftest.py` with the session fixture `transformed_db` and frozen dataclass `TransformedDb` (data-model.md §5): clone `template_db` into `transform_session`, `pipeline.run(Settings(database_url=url, source_url="file:///nonexistent/override-in-test.csv", raw_data_dir=<tmp_path_factory>/raw, log_level="DEBUG"), source_file=fixture_path("matches_transform.csv"), started_at=CONTRACT_STARTED_AT)`, capture `public_before = (helpers.snapshot(conn), helpers.catalog_snapshot(conn), alembic_version rows)`, `run_dbt(["build"], dbt_env(url, home, target))`, capture `public_after`, yield, then drop the database. The fixture never asserts on the build
- [x] T009 Run `scripts/check.sh`; it exits 0 (the session fixture is not used yet)

**Checkpoint**: harness ready; every story can build the fixture database.

---

## Phase 3: User Story 1 — Build analytics tables with one command (Priority: P1) 🎯 MVP

**Goal**: `dbt build` creates the staging layer in `analytics_staging`, runs its data tests, fails
loudly when a test fails, is idempotent, and leaves `public` untouched.

**Independent Test**: on the fixture database, `dbt build` exits 0. The three staging views exist
with the expected derivations, a second build is identical, `public` snapshots are equal, an
injected failing test gives exit 1 naming the test, and missing credentials give exit 2.

### Tests for User Story 1 (write first, expect failures)

- [ ] T010 [P] [US1] Write `tests/integration/test_transform_build.py`: `test_build_succeeds_and_runs_every_node` (`transformed_db.build.returncode == 0`; every status in `{"success", "pass"}`; the model nodes in `run_results` == the module-level literal `EXPECTED_MODELS` (US1: `{"stg_leagues", "stg_teams", "stg_matches"}`; T018 extends it to all 7) and == the `.sql` files under `transform/models`; the singular test names == the `.sql` files directly under `transform/tests`; at least one test node per model; `"deprecat" not in build.stdout.lower()`), `test_models_live_in_analytics_schemas` (`information_schema.tables`: every `stg_*` is a `VIEW` in `analytics_staging`, every `mart_*` a `BASE TABLE` in `analytics_marts`; nothing named `stg_*`/`mart_*` elsewhere; no schema `analytics`), `test_manifest_has_no_hooks_and_only_analytics_schemas` (manifest: every model node's `schema` in `{"analytics_staging", "analytics_marts"}`; every `config.pre-hook`/`post-hook` empty; no `operation.*` nodes; every source has `schema == "public"`), `test_every_model_has_key_and_relationship_tests` (from the manifest's test nodes `attached_node` and `test_metadata.name`/`kwargs`: every model has a `unique` + `not_null` key, or `unique_combination_of_columns` + `not_null` on each key column, matching data-model.md §2–§3 keys; every column listed as a foreign key in data-model.md has a `relationships` test to the stated model/field. Expected keys and foreign keys are literal dicts in the test, keyed by the models of `EXPECTED_MODELS`; a model in `EXPECTED_MODELS` with no matching tests fails, nothing is skipped), `test_public_tables_unchanged_by_build` (`public_before == public_after`, where both also include `SELECT version_num FROM alembic_version`), `test_build_without_credentials_exits_2` (`run_dbt(["build"], env)` with `DBT_ENV_SECRET_PG_PASSWORD` removed: return code 2, and the output mentions profile `football_analytics`; needs no database), `test_build_fails_and_names_failing_test` (research.md R18. Copy `transform/` to `tmp_path` with `shutil.copytree(ignore=ignore_patterns("target", "logs"))`; edit the copy's `models/staging/_staging.yml` as a YAML round trip: `yaml.safe_load`, find model `stg_matches`, column `home_points`, append `"unique"` to its `data_tests` list, `yaml.safe_dump`. On a fresh fixture database (template clone + ingest with `CONTRACT_STARTED_AT`), take `p0` = public snapshot + catalog + `alembic_version`. (1) `run_dbt(["build"], env, project_dir=copy)` gives return code 1, stdout contains `FAIL` and `unique_stg_matches_home_points`, that node's status is `"fail"`, and every model node downstream of `stg_matches` that is in `EXPECTED_MODELS` (from US2 on: the four marts) is `"skipped"`. (2) **Recovery on the same database**: `run_dbt(["build"], env)` with the unmodified `transform/` gives return code 0, `helpers.analytics_snapshot(conn)` equals `helpers.analytics_snapshot` of `transformed_db`, and the public snapshot equals `p0`. Both databases are built from the same template and fixture, so ids are equal), `test_build_on_empty_database_succeeds` (`empty_db_url` + `helpers.upgrade`: build exits 0 and every model in `ANALYTICS_MODELS` that exists has 0 rows), `test_build_succeeds_on_real_sample` (template clone + ingest `matches_real_sample.csv`: build exits 0, every status `success`/`pass`). Expected failure: model names missing from `EXPECTED_MODELS` / missing relations (an empty project builds with exit 0, so "the build failed" is not the expected failure)
- [ ] T011 [P] [US1] Write `tests/integration/test_transform_idempotency.py::test_second_build_is_identical`: own database (template clone + ingest `matches_transform.csv` with `CONTRACT_STARTED_AT`); `p0 = public snapshot + catalog`; build 1 (exit 0) → `a1 = analytics_snapshot`, `c1 = analytics_catalog_snapshot`, `p1`; build 2 (exit 0) → `a2`, `c2`, `p2`; assert `a1 == a2`, `c1 == c2`, `p0 == p1 == p2` (public snapshots include `alembic_version`), and `a1` covers every model in the build's manifest (non-empty for each model with fixture rows)
- [ ] T012 [P] [US1] Write the staging part of `tests/integration/test_transform_marts.py`: `test_fixture_match_ids_follow_source_order` (`SELECT match_id, source_row_number FROM matches ORDER BY match_id` gives pairs `(n, n)` for n = 1…15), `test_staging_derivations_match_expected` (`stg_matches` joined to `matches` on `match_id`, mapped to `source_row_number`, ordered by it == `expected["stg_matches"]`), `test_season_label_rule` (plan.md "Season rule cases": insert inside `conn.transaction()` with a forced rollback (raise and catch a private sentinel exception, or call `conn.rollback()` at the end); assert the 7 `(season, season_start_year)` pairs; afterwards `SELECT count(*) FROM matches` is still 15)

### Implementation for User Story 1

- [ ] T013 [US1] Create `transform/models/staging/stg_leagues.sql`, `stg_teams.sql`, `stg_matches.sql` and `transform/models/staging/_staging.yml` with the columns, derivations and generic tests of data-model.md §2 (makes T010–T012 pass with the staging layer only)
- [ ] T014 [US1] Run `scripts/check.sh`; it exits 0. Record the integration-step time in the commit message

**Checkpoint**: MVP: one command builds and tests staging; idempotent; `public` untouched.

---

## Phase 4: User Story 2 — Answer the core analytical questions (Priority: P1)

**Goal**: the four marts, with contracts, keys, generic and singular tests, match the
hand-calculated fixture exactly.

**Independent Test**: every mart's rows on the fixture equal `matches_transform.expected.json`. Every
singular test passes on the fixture and returns rows under its mutation.

### Tests for User Story 2 (write first, expect failures)

- [ ] T015 [P] [US2] Add the mart tests to `tests/integration/test_transform_marts.py` (each uses `helpers.mart_rows` and `load_expected("matches_transform.expected.json")`): `test_team_form_matches_expected`, `test_home_advantage_matches_expected`, `test_goals_per_season_matches_expected`, `test_team_ratings_matches_expected` (whole-list equality = SC-002); focused tests: `test_first_match_has_empty_form` (every row with `team_match_number == 1` has `form_matches 0` and three null sums, and there is one per team), `test_same_date_matches_ordered_by_match_id` (arsenal rows 4 and 5: numbers 4 and 5, and row 5's form includes row 4: `form_points` 8), `test_form_window_capped_at_five_previous` (`max(form_matches) == 5`; arsenal row 9 has `form_points 7`, `form_goals_for 8`, `form_goals_against 6`, i.e. matches 3–7 only), `test_shares_sum_to_one_within_rounding` (all rows `abs(sum - 1) <= Decimal("0.00015")`; SP1 2021/22 sums to exactly `Decimal("0.9999")`), `test_zero_count_shares_are_zero_not_null` (ARG 2021/22 `draw_share` and `away_win_share` are `"0.0000"`), `test_league_totals_equal_staged_goals` (per league-season `total_goals` == `sum(home_goals + away_goals)` of `stg_matches`, and `matches_played` == staged count), `test_unrated_team_has_no_rating_values` (river plate: `rated_matches 0` and six nulls; it still has 3 team-form rows), `test_excluded_rows_never_reach_marts` (no `mart_team_form` row on 2021-09-25; E0 2021/22 `matches_played == 7`; no Elo value `1999.0` in `mart_team_ratings`)
- [ ] T016 [P] [US2] Write `tests/integration/test_transform_invariants.py`: `MUTATIONS: tuple[tuple[str, str, str], ...]` = `(case_id, singular_test_name, sql)` for the 9 cases of data-model.md §4; `test_singular_test_detects_violation[case]` (parametrized, `ids=case_id`: on `transformed_db.url` open a connection, run `sql`, then `SELECT count(*) FROM (<compiled_sql(test_name)>) t`, assert ≥ 1, `conn.rollback()`; then in a new transaction the same count is 0, which proves the rollback), `test_singular_tests_cover_every_file` (`{name for _, name, _ in MUTATIONS}` == stems of `transform/tests/*.sql`, so every singular test has at least one case); the count query is `f"SELECT count(*) FROM (\n{sql}\n) t"`, `test_singular_tests_pass_on_fixture` (each singular test's status in the session build is `"pass"`)
- [ ] T017 [P] [US2] Add `tests/integration/test_transform_build.py::test_mart_columns_match_contract`: for each of the four marts, `information_schema.columns` `(column_name, data_type, numeric_precision, numeric_scale)` in `ordinal_position` order == data-model.md §3 (written out as a literal dict in the test); each mart has a primary key on exactly its §3 columns (`pg_constraint contype = 'p'`, columns via `pg_attribute`); the manifest has `contract.enforced` true for each mart

### Implementation for User Story 2

- [ ] T018 [US2] Extend `EXPECTED_MODELS` and the literal key/foreign-key dicts in `tests/integration/test_transform_build.py` to all 7 models (data-model.md §3), and switch the US1 `analytics_snapshot` calls to the default 7-model list (this makes `test_build_succeeds_and_runs_every_node` and `test_every_model_has_key_and_relationship_tests` fail until T019–T022); create `transform/tests/generic/unique_combination_of_columns.sql` (data-model.md §4, last paragraph)
- [ ] T019 [US2] Create `transform/models/marts/mart_team_form.sql` (CTE unpivot, `WINDOW prev5`, `team_match_number`) and its entry in `transform/models/marts/_marts.yml` (contract, constraints, every column with `data_type`, generic tests per data-model.md §3)
- [ ] T020 [US2] Create `transform/models/marts/mart_league_season_home_advantage.sql` and `mart_league_season_goals.sql` with their `_marts.yml` entries (data-model.md §3, research.md R13)
- [ ] T021 [US2] Create `transform/models/marts/mart_team_ratings.sql` (three `row_number()` windows with the tie orders of research.md R9; `LEFT JOIN` from `stg_teams`) and its `_marts.yml` entry
- [ ] T022 [US2] Create the six singular tests in `transform/tests/` named and formulated exactly as in data-model.md §4 (`assert_team_form_two_rows_per_match.sql`, `assert_team_form_matches_lag_recomputation.sql`, `assert_home_advantage_shares_sum_to_one.sql`, `assert_home_advantage_reconciles_with_staging.sql`, `assert_goals_reconcile_with_staging.sql`, `assert_team_ratings_reconcile_with_staging.sql`). Each one must use the independent formulation stated there, not copy the model's SQL (makes T015–T017 pass; T010/T011 still pass and now cover the marts)
- [ ] T023 [US2] Run `scripts/check.sh`; it exits 0. Confirm that `transformed_db.build` reports 75 nodes (7 models + 68 tests: staging 21 generic, marts 41 generic, 6 singular, per data-model.md §2–§4); if the count differs, reconcile it with data-model.md before continuing

**Checkpoint**: all four marts verified exactly; invariants proven non-vacuous.

---

## Phase 5: User Story 3 — Fast, measured queries (Priority: P2)

**Goal**: the team-form lookup index exists as versioned dbt config, and a reproducible measurement
procedure covers it, the primary keys, and the rejected candidates.

**Independent Test**: after a build, `mart_team_form` has an index on `(team_id, match_date,
match_id)`; Alembic head is still `0003`; the measurement script runs on the fixture database and
leaves the catalog unchanged.

### Tests for User Story 3 (write first, expect failures)

- [ ] T024 [US3] Add `tests/integration/test_transform_build.py::test_team_form_has_lookup_index` (`pg_indexes` for `analytics_marts.mart_team_form` has exactly one non-PK index whose definition ends with `USING btree (team_id, match_date, match_id)`), and `::test_explain_script_leaves_catalog_unchanged` (copy `scripts/explain_transforms.sql` into the testcontainers PostgreSQL container with `postgres_container.get_wrapped_container().put_archive("/tmp", <in-memory tar>)` and run `psql -v ON_ERROR_STOP=1 -U <user> -d <transformed_db name> -f /tmp/explain_transforms.sql` via `exec`; exit code 0; output contains each of the six `\echo` labels `Q1` … `Q6` (data-model.md §6); the number of `Execution Time:` lines equals 5 × the number of variants (11: Q1 2, Q2 2, Q3 2, Q4 1, Q5 2, Q6 2); `analytics_catalog_snapshot` and `helpers.catalog_snapshot` before == after. No plan-node assertions: at fixture size the planner's choice depends on uncontrolled statistics (plan review P1-4)). Expected failure: index missing / script missing

### Implementation for User Story 3

- [ ] T025 [US3] Add `{{ config(indexes=[{"columns": ["team_id", "match_date", "match_id"]}]) }}` to `transform/models/marts/mart_team_form.sql` (research.md R14)
- [ ] T026 [US3] Create `scripts/explain_transforms.sql` per data-model.md §6: header comment with usage (`psql -v ON_ERROR_STOP=1 -f`), the six queries, 5 × `EXPLAIN (ANALYZE, BUFFERS)` each, "before" or candidate variants inside `BEGIN … ROLLBACK` with names looked up at run time, an `\echo Qn: <label>` line before each query's runs, a header comment listing the variants per query, no planner overrides (`SET enable_*`), using only psql features available in the `postgres:17.6-alpine` image (makes T024 pass)
- [ ] T027 [US3] Run `scripts/check.sh`; it exits 0

**Checkpoint**: index versioned; measurement procedure ready for the real dataset (T036).

---

## Phase 6: User Story 4 — Run it in the containerized stack and CI (Priority: P2)

**Goal**: a `transform` Compose service (own image stage) builds and tests the models after
ingest; the smoke test and CI run it.

**Independent Test**: `scripts/smoke_compose.sh` builds both images, ingests `matches_valid.csv`,
runs `transform` twice (exit 0, counts 24/2/2/6 both times), and shows the app image has no dbt and
the transform image has no `football_stats`.

### Tests for User Story 4 (write first, expect failures)

- [ ] T028 [P] [US4] Add to `tests/unit/test_transform_project.py`: `test_compose_transform_service_uses_env_only` (`docker-compose.yml` service `transform`: `build.target == "transform"`; `"transform" in profiles`; `migrate` in `depends_on` and `ingest` not; no `env_file`; `environment` contains the five `DBT_*` names the profile reads (taken from `transform/profiles.yml`, not restated); every value is either a `${POSTGRES_*}` reference or a literal without credentials (`db`, a port number); the password variable is a `${…}` reference), `test_transform_group_is_default_and_excluded_from_app_image` (`Dockerfile` has stages `base`, `transform`, `app` in that order with `app` last; the `transform` stage runs `uv sync --frozen --only-group transform`; every `uv sync` in the `app` stage has `--no-default-groups` and none has `--no-dev` alone; `.env.example` defines the five `DBT_*` variables from the `POSTGRES_*` ones)

### Implementation for User Story 4

- [ ] T029 [US4] Restructure `Dockerfile` into stages per research.md R2, R15: `base` (python image, uv 0.12.5, `WORKDIR /app`, `COPY pyproject.toml uv.lock`, user `app` uid 10001); `transform` (`uv sync --frozen --only-group transform`, `COPY transform/ transform/`, `ENV PATH=/app/.venv/bin:$PATH DBT_TARGET_PATH=/tmp/dbt/target DBT_LOG_PATH=/tmp/dbt/logs DBT_SEND_ANONYMOUS_USAGE_STATS=false`, `USER app`, `WORKDIR /app/transform`, `CMD ["dbt", "build", "--project-dir", "/app/transform", "--profiles-dir", "/app/transform"]`); `app` last (today's steps, with `--no-dev` replaced by `--no-default-groups`)
- [ ] T030 [US4] Add the `transform` service to `docker-compose.yml` exactly as research.md R15 / T028, and the five host-run `DBT_*` lines to `.env.example` (contracts/transform.md) (makes T028 pass)
- [ ] T031 [US4] Extend `scripts/smoke_compose.sh`: build with `--profile ingest --profile transform`; in `cleanup` add `--profile transform`; after the fixture ingest run `"${COMPOSE[@]}" run --rm -T transform` twice. After each run, query the four mart counts with `psql -At` and assert `24|2|2|6` (plan.md "Smoke expectations"), equal both times. Assert `docker run --rm --entrypoint python football-stats:local -c "import dbt"` fails and `docker run --rm --entrypoint python football-stats-transform:local -c "import football_stats"` fails
- [ ] T032 [US4] In `.github/workflows/ci.yml` add step `Build transform image` (`docker build --target transform -t football-stats-transform:ci .`) after `Build image`
- [ ] T033 [US4] Run `scripts/check.sh` and `scripts/smoke_compose.sh`; both exit 0

**Checkpoint**: the transform runs in the stack, in the smoke test and in CI.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T034 [P] Write `tests/unit/test_readme_transform.py::test_readme_documents_transform`: README has a section `## Analytics transforms` naming `analytics_staging`, `analytics_marts` and the four marts; it states the season rule (contains `July` and `YYYY/YY`); it has a section `## Measured numbers - analytics transforms` with a table row for every index on the marts: the `indexes` config of `mart_team_form.sql`, parsed with a regex, plus one row per primary key, i.e. per mart in `_marts.yml`. It also has a row for each rejected candidate `matches (match_date)` and `matches (league_code, match_date)`. The README must not suggest creating indexes by hand (no `CREATE INDEX` outside a fenced block that also contains `ROLLBACK`). Expected failure: sections missing
- [ ] T035 Update `README.md`: architecture diagram (add `transform/` dbt → `analytics_staging` → `analytics_marts`); intro line naming feature 003; "Analytics transforms" section (what each mart answers, season rule, form window and tie rules, `docker compose run --rm ingest && docker compose run --rm transform`, host command, exit codes, link to contracts/marts.md, the "do not run during ingest" note, research.md R16, the failed-build state ("after a non-zero exit the analytics schemas are not trustworthy until the next exit-0 run; rerun to recover", research.md R18), and the "staging views block dropping referenced columns" trade-off, research.md R4); design-decision rows (dbt 1.11 pin, separate image stage, contracts, mutation-checked invariants, no Alembic index); "Measured numbers - analytics transforms" table with every row required by T034 and `TBD (T036)` in the measured cells; quality-gate paragraph mentions that dbt runs inside the integration tests (makes T034 pass)
- [ ] T036 **(manual, real data)** Run quickstart §1 and §2 on the full dataset: two timed `docker compose run --rm transform` (SC-001, SC-003), the planning-profile checks, the md5 comparison (SC-004), and `scripts/explain_transforms.sql` (FR-010, SC-005). Replace every `TBD (T036)` in the README with the measured values (median of 5, plan nodes before and after, machine), including the negative results. Record any difference from research.md R14/R17, and any rejected candidate that helps a staging/mart source query by more than 20 %, in `specs/003-dbt-transforms/decisions.md` as a plan deviation. Do not add an unplanned index
- [ ] T037 Measure `time scripts/check.sh` (target under about 60 s, research.md R5) and record it in the README quality-gate paragraph; run `scripts/check.sh` and `scripts/smoke_compose.sh` a final time; both exit 0

---

## Dependencies & Execution Order

- **Setup (T001–T004)** → **Foundational (T005–T009)** → **US1 (T010–T014)** → **US2 (T015–T023)**
  → **US3 (T024–T027)** and **US4 (T028–T033)** (independent of each other; both need US2 for
  marts) → **Polish (T034–T037)**.
- Within each story: test tasks first (they fail), then implementation in the listed order, then
  the gate.
- T008 needs T005 (fixture) and T007 (runner). T013 needs T010–T012. T022 needs T019–T021.
  T031 needs T029–T030. T036 needs T026 and T030.

### Parallel opportunities

- T002 can run alongside T001's verification once the lock is updated.
- T005 and T006 in parallel; then T007, then T008.
- US1 tests T010, T011, T012 in parallel (different files).
- US2 tests T015, T016, T017 in parallel; T020 and T021 in parallel after T019 (separate files; the
  shared `_marts.yml` entries are appended in order).
- US3 and US4 phases can proceed in parallel after US2.

## Implementation Strategy

1. **MVP = US1**: staging layer, one-command build, failure reporting, idempotency, `public`
   untouched. It is shippable on its own; marts come next.
2. **US2** completes the analytical value (both P1 stories), so the feature is useful after Phase 4.
3. **US3** and **US4** add the performance evidence and the container path.
4. **Polish** documents everything and records real measurements. Numbers are never estimated.
