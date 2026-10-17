# Implementation Plan: Analytics Transforms

**Branch**: `003-dbt-transforms` | **Date**: 2026-10-17 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-dbt-transforms/spec.md`

## Summary

Add a dbt project in `transform/` that turns the ingested `public` tables into a staging layer of
three views (`analytics_staging.stg_leagues`, `stg_teams`, `stg_matches`, which add season, per-side
result, points and total goals) and four contract-enforced mart tables in `analytics_marts`:
`mart_team_form` (team-match grain plus previous-5 form computed with a `ROWS BETWEEN 5 PRECEDING
AND 1 PRECEDING` window ordered by `match_date, match_id`), `mart_league_season_home_advantage`,
`mart_league_season_goals` and `mart_team_ratings` (latest, peak and lowest Elo; ties go to the
earliest date). `dbt build` builds every model and runs generic tests (keys, relationships,
accepted values), a project-local composite-key test, and six singular invariant tests. It exits
non-zero on any failure.

dbt-core/dbt-postgres **1.11.x** come in a new `transform` dependency group (research.md R1, R2) and
run in a separate `transform` image stage. The profile reads only environment variables. The
quality gate (`scripts/check.sh`, unchanged) gains integration tests that work like this: ingest a
hand-designed fixture with the real pipeline into testcontainers PostgreSQL, run `dbt build` once as
a subprocess, and compare every mart value exactly with hand-calculated expectations. Each singular
test is shown to catch its violation by a rolled-back mutation. Further tests prove that two builds
are identical and that `public` is untouched. One index is added, on `mart_team_form`, through dbt
config. Real-data measurements showed that candidate indexes on `public.matches` do not help the
transform, so there is no Alembic migration (research.md R14). A manual task measures and records
everything in the README. Compose gains a `transform` profile service; the smoke test and CI run it.

## Technical Context

**Language/Version**: Python 3.12 (unchanged) for tests; SQL (PostgreSQL 17 dialect) + Jinja for
dbt models.

**Primary Dependencies**: existing polars 1.44.2, psycopg 3.2, alembic 1.16, pandera 0.34.1
(unchanged). **New group `transform`**: `dbt-core>=1.11.15,<1.12` and `dbt-postgres>=1.11.0,<1.12`
(expected locks: 1.11.16 / 1.11.0; 35 new packages, no pandas/numpy). `[tool.uv] default-groups =
["dev", "transform"]`. **New dev**: `types-PyYAML`. Rationale and probe results: research.md R1,
R2.

**Storage**: PostgreSQL 17.6 (`postgres:17.6-alpine`). `public` unchanged (head
`0003_contract_constraints`); new dbt-owned schemas `analytics_staging` and `analytics_marts`.

**Testing**: pytest unit (pure, sockets disabled; static checks of `transform/`, `pyproject.toml`,
`Dockerfile`, `docker-compose.yml`, README) + integration (testcontainers PostgreSQL; dbt as a
subprocess, research.md R5). New fixture `tests/fixtures/matches_transform.csv` + `.expected.json`.

**Target Platform**: macOS (dev), Linux containers / GitHub Actions (CI), unchanged.

**Project Type**: single Python project (CLI + library) plus a dbt project directory.

**Performance Goals**: SC-001 full build < 2 min on real data (probe: 3.4 s of dbt time). The gate
stays under about 60 s (baseline 24 s; 8 dbt invocations of about 3 s each). Team-form lookup
under 1 ms with the index (probe: 0.02 ms, from 7.5 ms).

**Constraints**: no network in tests (no dbt packages, tracking off, never `dbt deps` or
`--version`); no clock or random in models; only `public` reads; config only from env vars; no
manual schema changes (indexes on marts come from versioned dbt config; on `public`, Alembic only).

**Scale/Scope**: 237,473 matches → 474,946 team-form rows, 789 league-seasons, 1,221 teams (783
rated). 7 models, 68 data tests (75 dbt nodes; the probe ran 71 with 4 fewer staging tests, research.md R17).

No `NEEDS CLARIFICATION` items remain.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / standard | How this plan complies | Evidence (test / artifact) |
|---|---|---|
| **I. No silent data loss** | Marts are derived from every loaded match. Reconciliation tests prove that league totals equal staging and that team-form has exactly two rows per match. Rows excluded by ingestion never appear, and that is asserted. Null Elo is excluded from rating aggregates only | `assert_*_reconcile_with_staging`, `assert_team_form_two_rows_per_match`, `test_excluded_rows_never_reach_marts`, `test_unrated_team_has_no_rating_values` |
| I. Explicit schema before clean layers | Unchanged for ingestion. Marts carry dbt model contracts (declared types and keys) | `test_mart_columns_match_contract` |
| I. Counts reconcile and are logged | dbt logs every node's status and row count (`SELECT n`); `run_results.json` is kept as an artifact | `test_build_succeeds_and_runs_every_node` |
| **II. Idempotent** | Full rebuild with atomic table swap; two builds give identical contents | `test_second_build_is_identical`; quickstart §1 on real data |
| II. Raw kept unchanged; layers rebuildable | `public` is read-only for dbt (no hooks, only `source()` reads); staging → marts can be rebuilt any time | `test_public_tables_unchanged_by_build`, `test_manifest_has_no_hooks_and_only_analytics_schemas` |
| II. Safe to retry | Each model is a full rebuild from `public`, so a rerun on the database a failed build left behind converges to a fresh build. The intermediate state after exit 1 is documented as "not trustworthy until the next exit-0 run" (research.md R18, contracts/transform.md) | `test_build_fails_and_names_failing_test`: failing build (exit 1, downstream `skipped`), then the unmodified rerun on the **same** database gives exit 0, `analytics_snapshot` equals the session build's, and `public` is unchanged |
| **III. Unit tests for transformation logic** | The logic is SQL. Its "unit" level is the exact fixture comparison per mart plus the singular invariants with mutation proofs. Python static checks are unit tests | `test_transform_marts.py`, `test_transform_invariants.py`, `tests/unit/test_transform_project.py` |
| III. Integration tests on real PostgreSQL | Every dbt test runs against testcontainers PostgreSQL 17.6 | `tests/integration/test_transform_*.py` |
| III. API tests | N/A (no API) | — |
| III. Done only when CI passes | CI runs check.sh (which includes dbt), both image builds, and the smoke test | `.github/workflows/ci.yml` |
| **IV. One-command stack** | `docker compose run --rm transform`; env-only config; secrets not committed (`DBT_ENV_SECRET_` scrubbed) | `scripts/smoke_compose.sh`, `test_profile_reads_only_env_vars`, `test_compose_transform_service_uses_env_only` |
| IV. Pinned versions | dbt pinned `<1.12` by range + lock hash; image from the same lock | `test_dbt_pinned_to_1_11` |
| **V. Simple, incremental** | dbt is mandated (PLAN, constitution "Transforms"). No dbt packages, no wrapper code in `src/`, no migration, one index. Four story phases, each ending green | research.md R3, R5, R14 |
| Tech: transforms in SQL, staging → marts | `models/staging` views → `models/marts` tables | `test_models_layered_staging_and_marts`, `test_models_live_in_analytics_schemas` |
| Tech: versioned migrations, PKs/FKs/indexes | No `public` change (Alembic head stays `0003`). Marts: PKs via enforced contracts; the lookup index via versioned dbt config (justified interpretation, Complexity Tracking row 1, spec Assumptions); no DB foreign keys, with `relationships` tests instead (justified deviation, Complexity Tracking row 2) | `test_team_form_has_lookup_index`, `test_mart_columns_match_contract`, `test_every_model_has_key_and_relationship_tests`, `test_upgrade_is_noop_at_head` (still `0003`) |
| Quality: typed, docstrings, logging, no bare except | New Python is test code only (`dbt_runner.py`, helpers): typed under `mypy --strict`, docstrings, `logging` | gate steps 1–3 |
| Quality: pure logic, one module per stage | No `src/` change | — |
| Workflow: measured performance | SC-001 and every index measured on the full dataset, with negative results, recorded in the README | T036 (quickstart §1–2), `test_readme_documents_transform` |
| Workflow: README current | New "Analytics transforms" section, architecture diagram updated | `test_readme_documents_transform` |

**Result (pre-design)**: PASS. **Result (post-design re-check)**: PASS, with two justified
deviations or interpretations of "Schema design / migrations", both listed in Complexity Tracking:
(1) indexes and keys of dbt-owned tables are defined in dbt model config; (2) marts have no database
foreign keys.

## Deterministic Quality Gate

`scripts/check.sh` **already exists** (since 001 T001) and is **not modified**. It runs, in order,
and exits non-zero on the first failure:

1. `uv run --frozen ruff format --no-cache --check .`
2. `uv run --frozen ruff check --no-cache .`
3. `uv run --frozen mypy src tests` (strict)
4. `uv run --frozen pytest tests/unit --randomly-seed=20261011 --disable-socket`
5. `uv run --frozen pytest tests/integration --randomly-seed=20261011` (real PostgreSQL 17.6 via
   testcontainers; now also runs `dbt build` as a subprocess)

dbt is available to steps 4–5 because `transform` is a default uv group (research.md R2). The first
task (T001) adds the dependency and confirms the gate is green. T003 creates the dbt project
skeleton. Every phase ends with the gate green. Test-writing tasks are expected to fail for the
stated reason until their paired implementation task.

Determinism rules for this feature (in addition to 001/002's):
- **No network**: dbt tracking off; no `packages.yml`/`dependencies.yml`; tests never call
  `dbt deps` or `dbt --version`; all data are fixtures under `tests/fixtures/`; the real-data run is
  manual (T036). Guarded by `test_project_is_offline_and_deterministic`.
- **No clock or random**: no `now()`, `current_date`, `random()` or `nextval` in `transform/`
  (`test_models_use_no_volatile_functions`). Fixture ingests use `started_at=CONTRACT_STARTED_AT`
  (all fixture dates ≤ 2022-08-06).
- **Total orders**: every window and every "first row" choice ends with `match_id` (research.md R8,
  R9). Snapshots order by full keys.
- **Explicit environment**: dbt subprocesses get a fresh environment (`dbt_env`), a `tmp_path`
  `HOME`, and target and log dirs, so nothing leaks from the developer's shell or `~/.dbt`.
  `partial_parse: false`.
- **Order independence**: tests that read the session build only read. Mutation checks run inside a
  rolled-back transaction. Every test that builds gets its own database.
- **Catalog names ignored**: dbt index and primary-key names change per build (time-based hash,
  tmp-table swap), so catalog comparisons strip names (research.md R11, R12).

## FR / SC → Test Mapping

Unit tests in `tests/unit/`, integration tests in `tests/integration/`. Names are exact and are
referenced by tasks.md. `[case]` = parametrized over `test_transform_invariants.MUTATIONS`.

| Req | What is verified | Test(s) (file :: name) |
|---|---|---|
| FR-001 | SQL models layered staging (views) → marts (tables); separate schemas; nothing in `public` | `unit/test_transform_project.py::test_models_layered_staging_and_marts`; `integration/test_transform_build.py::test_models_live_in_analytics_schemas`, `::test_manifest_has_no_hooks_and_only_analytics_schemas` |
| FR-002 | staging derives season, per-side result, points, total goals | `integration/test_transform_marts.py::test_staging_derivations_match_expected` |
| FR-003 | July cut-over season label, incl. `1999/00` wrap and Jun 30/Jul 1 boundary; documented | `integration/test_transform_marts.py::test_season_label_rule`, `::test_staging_derivations_match_expected` (fixture rows 2/3); `unit/test_readme_transform.py::test_readme_documents_transform` (rule text) |
| FR-004 | four marts exist with the documented columns and values | `integration/test_transform_build.py::test_mart_columns_match_contract`; `integration/test_transform_marts.py::test_team_form_matches_expected`, `::test_home_advantage_matches_expected`, `::test_goals_per_season_matches_expected`, `::test_team_ratings_matches_expected` |
| FR-005 | window over strictly earlier matches, ≤ 5, date then `match_id` | `integration/test_transform_marts.py::test_first_match_has_empty_form`, `::test_same_date_matches_ordered_by_match_id`, `::test_form_window_capped_at_five_previous`, `::test_fixture_match_ids_follow_source_order`; `integration/test_transform_invariants.py::test_singular_test_detects_violation[form_includes_current_match]` |
| FR-006 | key + relationship tests on every model; business-invariant tests on marts, each shown to detect its violation | `integration/test_transform_build.py::test_every_model_has_key_and_relationship_tests`; `integration/test_transform_invariants.py::test_singular_test_detects_violation[case]`, `::test_singular_tests_cover_every_file`, `::test_singular_tests_pass_on_fixture` |
| FR-007 | two builds identical; `public` (incl. `alembic_version`) untouched; rerun after a failed build converges | `integration/test_transform_idempotency.py::test_second_build_is_identical`; `integration/test_transform_build.py::test_build_fails_and_names_failing_test` (recovery part); `integration/test_transform_build.py::test_public_tables_unchanged_by_build`, `::test_manifest_has_no_hooks_and_only_analytics_schemas` |
| FR-008 | one command, env-only config, compose service | `unit/test_transform_project.py::test_profile_reads_only_env_vars`, `::test_compose_transform_service_uses_env_only`, `::test_transform_group_is_default_and_excluded_from_app_image`; `integration/test_transform_build.py::test_build_without_credentials_exits_2`; `scripts/smoke_compose.sh` (CI) |
| FR-009 | mart values = hand-calculated fixture, in the gate | the four `*_matches_expected` tests above + `::test_staging_derivations_match_expected` |
| FR-010 | added index is versioned; no hand-made schema change; measured | `integration/test_transform_build.py::test_team_form_has_lookup_index`, `::test_explain_script_leaves_catalog_unchanged` (measurement procedure is read-only); `integration/test_migrations.py::test_upgrade_is_noop_at_head` (001/002, unchanged: still `0003`); `unit/test_readme_transform.py::test_readme_documents_transform` (every index has a README row); numbers: **manual** T036 |
| SC-001 | full real build < 2 min, measured | **Manual** — T036 (quickstart §1), README |
| SC-002 | 100 % of fixture mart values match | `integration/test_transform_marts.py::test_team_form_matches_expected`, `::test_home_advantage_matches_expected`, `::test_goals_per_season_matches_expected`, `::test_team_ratings_matches_expected` (whole-table list equality) |
| SC-003 | 0 data-test failures on real data | **Manual** — T036 (quickstart §1); automated proxy on real rows: `integration/test_transform_build.py::test_build_succeeds_on_real_sample` |
| SC-004 | two builds identical in 100 % of models | `integration/test_transform_idempotency.py::test_second_build_is_identical` (all 7 models + analytics catalog); real data: quickstart §1 md5 |
| SC-005 | each index has a measured before/after in the README | `unit/test_readme_transform.py::test_readme_documents_transform`; numbers **manual** T036 |
| US1-AS1 | build creates models and all tests pass | `integration/test_transform_build.py::test_build_succeeds_and_runs_every_node` |
| US1-AS2 | rerun identical | `integration/test_transform_idempotency.py::test_second_build_is_identical` |
| US1-AS3 | failing test → non-zero exit, names the test, downstream skipped; same-DB rerun recovers | `integration/test_transform_build.py::test_build_fails_and_names_failing_test` |
| US1-AS4 | ingestion tables unchanged | `integration/test_transform_build.py::test_public_tables_unchanged_by_build` |
| US2-AS1 | form covers only earlier matches, ≤ 5, empty first | `::test_first_match_has_empty_form`, `::test_form_window_capped_at_five_previous`, `::test_team_form_matches_expected` |
| US2-AS2 | shares sum to 100 % within rounding; count = loaded matches | `integration/test_transform_marts.py::test_shares_sum_to_one_within_rounding`, `::test_excluded_rows_never_reach_marts` |
| US2-AS3 | totals = sum of full-time goals | `integration/test_transform_marts.py::test_league_totals_equal_staged_goals` |
| US2-AS4 | ratings correct with dates; unrated team has none | `::test_team_ratings_matches_expected`, `::test_unrated_team_has_no_rating_values` |
| US3-AS1/2 | README lists index, query, before/after; versioned | as FR-010 / SC-005 |
| US4-AS1 | service builds and tests every model, exit 0 | `scripts/smoke_compose.sh` (two transform runs, counts, image separation) |
| US4-AS2 | CI builds and tests models on a real DB | `.github/workflows/ci.yml` runs `scripts/check.sh` (integration tests above) |
| Edge: same-date matches | deterministic order | `::test_same_date_matches_ordered_by_match_id` |
| Edge: calendar-year league | rule applied consistently | `::test_staging_derivations_match_expected` (ARG rows 10–12) |
| Edge: zero draws / away wins | shares 0, not null | `integration/test_transform_marts.py::test_zero_count_shares_are_zero_not_null` |
| Edge: missing Elo | excluded from ratings only, counted elsewhere | `::test_unrated_team_has_no_rating_values`, `::test_team_form_matches_expected` (rows 6, 8–15 present) |
| Edge: empty database | build succeeds, marts empty | `integration/test_transform_build.py::test_build_on_empty_database_succeeds` |
| Design: offline/deterministic | flags, no packages, no volatile functions | `unit/test_transform_project.py::test_project_is_offline_and_deterministic`, `::test_models_use_no_volatile_functions` |
| Design: dbt pin | `<1.12` (experimental-parser download) | `unit/test_transform_project.py::test_dbt_pinned_to_1_11` |
| Design: test env isolation | `dbt_env` passes only connection vars, decodes URL | `unit/test_dbt_runner.py::test_dbt_env_parses_url_and_passes_nothing_else` |

## Test Fixtures and Shared Test Data

### `tests/fixtures/matches_transform.csv` (new, synthetic)

Real 48-column header. Every row is base row **B** (data row 1 of `tests/fixtures/matches_valid.csv`)
with `HTHome`, `HTAway`, `HTResult` **empty**, so the half-time rules are skipped, and the
overrides below. Goals are written like the source (`2.0`); empty Elo cells are empty strings.
`MatchTime` stays `15:00:00`. All dates ≤ 2022-08-06 (before `CONTRACT_STARTED_AT`). Team keys are
the casefolded names.

| Row | Division | MatchDate | HomeTeam | AwayTeam | FT | FTResult | HomeElo | AwayElo | Purpose |
|---:|---|---|---|---|---|---|---|---|---|
| 1 | E0 | 2021-05-01 | Arsenal | Chelsea | 2-1 | H | 1600.0 | 1550.0 | Arsenal's first match (empty form); 2020/21 |
| 2 | E0 | 2021-06-30 | Chelsea | Arsenal | 0-0 | D | 1540.0 | 1610.0 | Jun 30 → 2020/21; Chelsea's lowest |
| 3 | E0 | 2021-07-01 | Arsenal | Leeds | 3-0 | H | 1620.0 | 1450.0 | Jul 1 → 2021/22; Leeds' peak |
| 4 | E0 | 2021-08-14 | Arsenal | Wolves | 1-1 | D | 1650.0 | 1500.0 | same date as row 5 (lower `match_id` first); Arsenal peak (tie, earliest) |
| 5 | E0 | 2021-08-14 | Leeds | Arsenal | 0-2 | A | 1440.0 | 1645.0 | same-date second match |
| 6 | E0 | 2021-08-21 | Wolves | Arsenal | 2-1 | H | (empty) | 1640.0 | Wolves unrated here |
| 7 | E0 | 2021-08-28 | Arsenal | Chelsea | 1-3 | A | 1630.0 | 1560.0 | Arsenal's 7th match: window = matches 2–6 |
| 8 | E0 | 2021-09-11 | Chelsea | Leeds | 2-2 | D | 1570.0 | 1435.0 | Leeds' latest *rated* match |
| 9 | E0 | 2021-09-18 | Arsenal | Leeds | 4-1 | H | 1650.0 | (empty) | Arsenal's 8th: window = 3–7; peak tie with row 4; Leeds unrated here |
| 10 | ARG | 2022-02-05 | Boca Juniors | River Plate | 2-0 | H | 1700.0 | (empty) | calendar-year league, Feb → 2021/22 |
| 11 | ARG | 2022-03-12 | River Plate | Boca Juniors | 3-1 | H | (empty) | (empty) | |
| 12 | ARG | 2022-08-06 | Boca Juniors | River Plate | 1-0 | H | 1700.0 | (empty) | Aug → 2022/23; Boca peak/lowest tie (earliest = 2022-02-05) |
| 13 | SP1 | 2021-09-12 | Real Madrid | Barcelona | 2-1 | H | (empty) | (empty) | three-outcome league-season (shares sum 0.9999) |
| 14 | SP1 | 2021-09-19 | Barcelona | Sevilla | 1-1 | D | (empty) | (empty) | |
| 15 | SP1 | 2021-09-26 | Sevilla | Real Madrid | 0-1 | A | (empty) | (empty) | |
| 16 | E0 | 2021-09-25 | Wolves | Chelsea | 1-0 | **A** | 1999.0 | 1999.0 | `ft_result_consistent` violation → excluded by ingestion; must not reach any mart (its 1999.0 Elo would otherwise become Wolves'/Chelsea's peak) |

Ingest (contract v1, `started_at=CONTRACT_STARTED_AT`): read 16, loaded 15, excluded 1
(`contract_violation`). On a fresh database, `match_id` = source row number 1–15
(`test_fixture_match_ids_follow_source_order`). Teams (9): arsenal, barcelona, boca juniors,
chelsea, leeds, real madrid, river plate, sevilla, wolves.

### `tests/fixtures/matches_transform.expected.json` (new)

One object with five keys; every list is in the order shown. Rows use natural keys:
`source_row_number` for matches, `team_key` for teams. Numerics are strings exactly as
`numeric::text`, Elo values are JSON numbers, dates are ISO strings, and missing values are `null`.
All values below were computed by a separate plain-Python script and matched the probe build
(research.md, "Fixture probe").

**`stg_matches`** (order: `source_row_number`), keys `source_row_number, season,
season_start_year, home_result, away_result, home_points, away_points, total_goals`:
1 `2020/21` 2020 W L 3 0 3 · 2 `2020/21` 2020 D D 1 1 0 · 3 `2021/22` 2021 W L 3 0 3 ·
4 `2021/22` 2021 D D 1 1 2 · 5 `2021/22` 2021 L W 0 3 2 · 6 `2021/22` 2021 W L 3 0 3 ·
7 `2021/22` 2021 L W 0 3 4 · 8 `2021/22` 2021 D D 1 1 4 · 9 `2021/22` 2021 W L 3 0 5 ·
10 `2021/22` 2021 W L 3 0 2 · 11 `2021/22` 2021 W L 3 0 4 · 12 `2022/23` 2022 W L 3 0 1 ·
13 `2021/22` 2021 W L 3 0 3 · 14 `2021/22` 2021 D D 1 1 2 · 15 `2021/22` 2021 L W 0 3 1.

**`mart_team_form`** (order: `team_key, match_date, source_row_number`), keys `team_key,
source_row_number, opponent_team_key, league_code, season, season_start_year, match_date, is_home,
goals_for, goals_against, result, points, team_match_number, form_matches, form_points,
form_goals_for, form_goals_against`. `league_code` and `season_start_year` follow from the row
table and the staged values above.

| team_key | row | opponent | season | date | home | GF | GA | result | pts | # | form_matches | form_points | form_GF | form_GA |
|---|---:|---|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|
| `arsenal` | 1 | `chelsea` | 2020/21 | 2021-05-01 | H | 2 | 1 | W | 3 | 1 | 0 | null | null | null |
| `arsenal` | 2 | `chelsea` | 2020/21 | 2021-06-30 | A | 0 | 0 | D | 1 | 2 | 1 | 3 | 2 | 1 |
| `arsenal` | 3 | `leeds` | 2021/22 | 2021-07-01 | H | 3 | 0 | W | 3 | 3 | 2 | 4 | 2 | 1 |
| `arsenal` | 4 | `wolves` | 2021/22 | 2021-08-14 | H | 1 | 1 | D | 1 | 4 | 3 | 7 | 5 | 1 |
| `arsenal` | 5 | `leeds` | 2021/22 | 2021-08-14 | A | 2 | 0 | W | 3 | 5 | 4 | 8 | 6 | 2 |
| `arsenal` | 6 | `wolves` | 2021/22 | 2021-08-21 | A | 1 | 2 | L | 0 | 6 | 5 | 11 | 8 | 2 |
| `arsenal` | 7 | `chelsea` | 2021/22 | 2021-08-28 | H | 1 | 3 | L | 0 | 7 | 5 | 8 | 7 | 3 |
| `arsenal` | 9 | `leeds` | 2021/22 | 2021-09-18 | H | 4 | 1 | W | 3 | 8 | 5 | 7 | 8 | 6 |
| `barcelona` | 13 | `real madrid` | 2021/22 | 2021-09-12 | A | 1 | 2 | L | 0 | 1 | 0 | null | null | null |
| `barcelona` | 14 | `sevilla` | 2021/22 | 2021-09-19 | H | 1 | 1 | D | 1 | 2 | 1 | 0 | 1 | 2 |
| `boca juniors` | 10 | `river plate` | 2021/22 | 2022-02-05 | H | 2 | 0 | W | 3 | 1 | 0 | null | null | null |
| `boca juniors` | 11 | `river plate` | 2021/22 | 2022-03-12 | A | 1 | 3 | L | 0 | 2 | 1 | 3 | 2 | 0 |
| `boca juniors` | 12 | `river plate` | 2022/23 | 2022-08-06 | H | 1 | 0 | W | 3 | 3 | 2 | 3 | 3 | 3 |
| `chelsea` | 1 | `arsenal` | 2020/21 | 2021-05-01 | A | 1 | 2 | L | 0 | 1 | 0 | null | null | null |
| `chelsea` | 2 | `arsenal` | 2020/21 | 2021-06-30 | H | 0 | 0 | D | 1 | 2 | 1 | 0 | 1 | 2 |
| `chelsea` | 7 | `arsenal` | 2021/22 | 2021-08-28 | A | 3 | 1 | W | 3 | 3 | 2 | 1 | 1 | 2 |
| `chelsea` | 8 | `leeds` | 2021/22 | 2021-09-11 | H | 2 | 2 | D | 1 | 4 | 3 | 4 | 4 | 3 |
| `leeds` | 3 | `arsenal` | 2021/22 | 2021-07-01 | A | 0 | 3 | L | 0 | 1 | 0 | null | null | null |
| `leeds` | 5 | `arsenal` | 2021/22 | 2021-08-14 | H | 0 | 2 | L | 0 | 2 | 1 | 0 | 0 | 3 |
| `leeds` | 8 | `chelsea` | 2021/22 | 2021-09-11 | A | 2 | 2 | D | 1 | 3 | 2 | 0 | 0 | 5 |
| `leeds` | 9 | `arsenal` | 2021/22 | 2021-09-18 | A | 1 | 4 | L | 0 | 4 | 3 | 1 | 2 | 7 |
| `real madrid` | 13 | `barcelona` | 2021/22 | 2021-09-12 | H | 2 | 1 | W | 3 | 1 | 0 | null | null | null |
| `real madrid` | 15 | `sevilla` | 2021/22 | 2021-09-26 | A | 1 | 0 | W | 3 | 2 | 1 | 3 | 2 | 1 |
| `river plate` | 10 | `boca juniors` | 2021/22 | 2022-02-05 | A | 0 | 2 | L | 0 | 1 | 0 | null | null | null |
| `river plate` | 11 | `boca juniors` | 2021/22 | 2022-03-12 | H | 3 | 1 | W | 3 | 2 | 1 | 0 | 0 | 2 |
| `river plate` | 12 | `boca juniors` | 2022/23 | 2022-08-06 | A | 0 | 1 | L | 0 | 3 | 2 | 3 | 3 | 3 |
| `sevilla` | 14 | `barcelona` | 2021/22 | 2021-09-19 | A | 1 | 1 | D | 1 | 1 | 0 | null | null | null |
| `sevilla` | 15 | `real madrid` | 2021/22 | 2021-09-26 | H | 0 | 1 | L | 0 | 2 | 1 | 1 | 1 | 1 |
| `wolves` | 4 | `arsenal` | 2021/22 | 2021-08-14 | A | 1 | 1 | D | 1 | 1 | 0 | null | null | null |
| `wolves` | 6 | `arsenal` | 2021/22 | 2021-08-21 | H | 2 | 1 | W | 3 | 2 | 1 | 1 | 1 | 1 |

**`mart_league_season_home_advantage`** (order: `league_code, season`):

| league | season | start | played | H | D | A | home_win_share | draw_share | away_win_share | avg_home_goals | avg_away_goals |
|---|---|---:|---:|---:|---:|---:|---|---|---|---|---|
| ARG | 2021/22 | 2021 | 2 | 2 | 0 | 0 | 1.0000 | 0.0000 | 0.0000 | 2.5000 | 0.5000 |
| ARG | 2022/23 | 2022 | 1 | 1 | 0 | 0 | 1.0000 | 0.0000 | 0.0000 | 1.0000 | 0.0000 |
| E0 | 2020/21 | 2020 | 2 | 1 | 1 | 0 | 0.5000 | 0.5000 | 0.0000 | 1.0000 | 0.5000 |
| E0 | 2021/22 | 2021 | 7 | 3 | 2 | 2 | 0.4286 | 0.2857 | 0.2857 | 1.8571 | 1.4286 |
| SP1 | 2021/22 | 2021 | 3 | 1 | 1 | 1 | 0.3333 | 0.3333 | 0.3333 | 1.0000 | 1.0000 |

**`mart_league_season_goals`** (order: `league_code, season`):

| league | season | start | played | total_goals | goals_per_match | over_2_5 | over_2_5_share | btts | btts_share |
|---|---|---:|---:|---:|---|---:|---|---:|---|
| ARG | 2021/22 | 2021 | 2 | 6 | 3.0000 | 1 | 0.5000 | 1 | 0.5000 |
| ARG | 2022/23 | 2022 | 1 | 1 | 1.0000 | 0 | 0.0000 | 0 | 0.0000 |
| E0 | 2020/21 | 2020 | 2 | 3 | 1.5000 | 1 | 0.5000 | 1 | 0.5000 |
| E0 | 2021/22 | 2021 | 7 | 23 | 3.2857 | 5 | 0.7143 | 5 | 0.7143 |
| SP1 | 2021/22 | 2021 | 3 | 6 | 2.0000 | 1 | 0.3333 | 2 | 0.6667 |

**`mart_team_ratings`** (order: `team_key`), keys `team_key, team_name, rated_matches, latest_elo,
latest_elo_date, peak_elo, peak_elo_date, lowest_elo, lowest_elo_date`:

| team_key | name | rated | latest | latest date | peak | peak date | lowest | lowest date |
|---|---|---:|---:|---|---:|---|---:|---|
| `arsenal` | Arsenal | 8 | 1650.0 | 2021-09-18 | 1650.0 | 2021-08-14 | 1600.0 | 2021-05-01 |
| `barcelona` | Barcelona | 0 | null | null | null | null | null | null |
| `boca juniors` | Boca Juniors | 2 | 1700.0 | 2022-08-06 | 1700.0 | 2022-02-05 | 1700.0 | 2022-02-05 |
| `chelsea` | Chelsea | 4 | 1570.0 | 2021-09-11 | 1570.0 | 2021-09-11 | 1540.0 | 2021-06-30 |
| `leeds` | Leeds | 3 | 1435.0 | 2021-09-11 | 1450.0 | 2021-07-01 | 1435.0 | 2021-09-11 |
| `real madrid` | Real Madrid | 0 | null | null | null | null | null | null |
| `river plate` | River Plate | 0 | null | null | null | null | null | null |
| `sevilla` | Sevilla | 0 | null | null | null | null | null | null |
| `wolves` | Wolves | 1 | 1500.0 | 2021-08-14 | 1500.0 | 2021-08-14 | 1500.0 | 2021-08-14 |

### Season rule cases (`test_season_label_rule`)

Inside one rolled-back transaction on `transformed_db`: insert matches `E0`, arsenal v chelsea,
`2-1 H`, `source_sha256` = 64 × `'0'`, `source_row_number` 1001+, dated 2000-01-01, 2000-06-30,
2000-07-01, 2009-12-31, 2010-01-01, 2019-06-30, 2019-07-01 (all inside the `date_in_range`
contract). Then read `season, season_start_year` from the live view `analytics_staging.stg_matches`.
Expected: `1999/00` 1999, `1999/00` 1999, `2000/01` 2000, `2009/10` 2009, `2009/10` 2009,
`2018/19` 2018, `2019/20` 2019.

### Smoke expectations (`scripts/smoke_compose.sh`, fixture `matches_valid.csv`, 12 matches)

`mart_team_form` 24, `mart_league_season_home_advantage` 2, `mart_league_season_goals` 2,
`mart_team_ratings` 6 (probe-verified), identical after the second transform run.

## Project Structure

### Documentation (this feature)

```text
specs/003-dbt-transforms/
├── plan.md              # This file
├── research.md          # Phase 0: decisions R1–R17
├── data-model.md        # Phase 1: sources, staging, marts, singular tests, helpers, measurement
├── quickstart.md        # Phase 1: gate, real-data build, index measurement
├── contracts/
│   ├── transform.md     # command, env vars, exit codes, outputs
│   └── marts.md         # consumer interface of analytics_marts
├── decisions.md         # review responses + T036 deviations (created by reviews / T036)
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root; new = +, changed = ~)

```text
pyproject.toml, uv.lock          ~ group transform (dbt-core, dbt-postgres), default-groups, types-PyYAML
Dockerfile                       ~ multi-stage: base, transform (dbt only), app (last; --no-default-groups)
docker-compose.yml               ~ + transform service (profile transform)
.env.example                     ~ + DBT_PG_* for host runs
.gitignore, .dockerignore        ~ + transform/target/, transform/logs/
transform/                       + dbt project
├── dbt_project.yml              + name, flags, materializations/schemas per folder
├── profiles.yml                 + env_var-only profile
├── models/staging/              + _sources.yml, _staging.yml, stg_leagues.sql, stg_teams.sql, stg_matches.sql
├── models/marts/                + _marts.yml, mart_team_form.sql, mart_league_season_home_advantage.sql,
│                                  mart_league_season_goals.sql, mart_team_ratings.sql
└── tests/                       + generic/unique_combination_of_columns.sql, 6 × assert_*.sql
scripts/
├── check.sh                     (unchanged)
├── smoke_compose.sh             ~ transform runs, counts, image separation
└── explain_transforms.sql       + measurement procedure (data-model.md §6)
.github/workflows/ci.yml         ~ + transform image build
src/football_stats/              (unchanged)
migrations/                      (unchanged; head 0003)
tests/
├── fixtures/                    + matches_transform.csv, matches_transform.expected.json; ~ SOURCES.txt
├── unit/                        + test_transform_project.py, test_dbt_runner.py, test_readme_transform.py
└── integration/
    ├── dbt_runner.py            + subprocess runner, DbtResult, dbt_env
    ├── helpers.py               ~ analytics_snapshot, analytics_catalog_snapshot, mart_rows, ANALYTICS_KEYS
    ├── conftest.py              ~ session fixture transformed_db
    ├── test_transform_build.py  +
    ├── test_transform_marts.py  +
    ├── test_transform_invariants.py  +
    └── test_transform_idempotency.py +
README.md                        ~ architecture, transform section, measured numbers
```

**Structure Decision**: the single-project layout stays. The dbt project is a sibling directory,
`transform/`, because it is a separate tool with its own file conventions, not Python package code
(research.md R3). No Python is added to `src/`.

## Complexity Tracking

Two justified constitution deviations or interpretations (Technical Constraints: "Schema changes MUST
be made through versioned migrations"; "tables MUST declare primary keys, foreign keys, and
appropriate indexes"), followed by other items a reviewer might question:

| Item | Why needed | Simpler alternative rejected because |
|---|---|---|
| **Deviation 1 (interpretation)**: mart indexes and keys come from dbt model config, not Alembic | Marts are dropped and recreated by every build, so an Alembic index on them would vanish on the next run. The dbt model file is git-versioned and applied by every build, never by hand (US3-AS2). Recorded in spec.md Assumptions (plan review R-A): for dbt-owned schemas the dbt model definition is the versioned structure change; `public` stays Alembic-only | an Alembic migration cannot own tables it does not create, and would make Alembic depend on dbt having run; a post-hook `CREATE INDEX` is a hook (R4, R14) |
| **Deviation 2**: marts declare no database foreign keys; `relationships` tests replace them | An FK from a mart to `public` (e.g. `mart_team_form.match_id → public.matches`) would make ingestion's own DML depend on analytics: 002's convergence `DELETE` of removed matches would fail, or with `ON DELETE CASCADE` reach into analytics. That breaks the ownership split (R4) and the spirit of FR-007. FKs *between* marts are blocked by dbt's per-table tmp-table swap: the referenced table is renamed and dropped during a rebuild, and dropping it would require dropping the dependent FK | FKs to `public`: couple ingestion to analytics; FKs between marts: incompatible with the swap materialization. The `relationships` tests check the same reference on every build (data-model.md §2–§3) |
| dbt 1.11 rather than the latest 1.12 | 1.12 installs an un-hashed GitHub binary at build time (failed in the probe) | 1.12 breaks offline/locked builds (R1) |
| Second Docker stage and image | Keeps dbt out of the app image and polars out of the transform image (430 MB vs about 680 MB combined); one lock for both | a single image carries both toolchains; a separate Dockerfile/lock could drift (R2) |
| Model contracts on marts | The marts are the Phase 5 interface; a type or key drift fails the build in the container too | pytest-only type checks would not run on real data (R11) |
| Mutation checks for singular tests | Prove every invariant test can fail; cheap (rolled-back transactions) | without them a vacuous test (e.g. a wrong join) passes forever (R5, R10) |
| Project-local generic test `unique_combination_of_columns` | Composite keys need a test (FR-006); dbt has no built-in | dbt-utils needs `dbt deps` (network) or vendoring (R5) |
| `types-PyYAML` dev dependency | Unit tests parse dbt YAML under `mypy --strict` | regex-parsing YAML is fragile |
