# Implementation Plan: Match Data Contract

**Branch**: `002-data-contract` | **Date**: 2026-10-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-data-contract/spec.md`

## Summary

Add a versioned, declarative **data contract** (nine cross-field business rules) on top of feature
001's per-column checks. The contract lives in one module, `data_contract.py`, as rule objects that
compile to a pandera polars `DataFrameSchema`. pandera evaluates it vectorized on the rows that
passed 001's checks; its failure cases become per-row lists of violated rule ids. Violating rows are
excluded with the new reason `contract_violation` (precedence: after `same_team`, before
`duplicate`), and every run records the contract version and violations per rule. Two Alembic
revisions follow. `0002_contract_run_metadata` adds the run-record columns and the new reason code.
`0003_contract_constraints` adds nine named CHECK constraints `NOT VALID`, so the database rejects
any new or updated violating match. The migration still succeeds on a database that already holds
violators. Re-running ingestion on the same source file **converges**, all inside 001's single
transaction: it deletes this file's matches that are no longer accepted and this file's exclusion
records that no longer apply, then deletes teams and leagues that no match references. The result
equals a fresh build. The existing deterministic gate `scripts/check.sh` is unchanged and must stay
green after every task. Decisions: [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.12 (unchanged).

**Primary Dependencies**: existing polars 1.x (locked 1.44.2), psycopg 3.2, alembic 1.16;
**new**: `pandera[polars]>=0.34.1,<0.35` (locked 0.34.1; brings pydantic 2, typeguard; no
pandas/numpy). Rationale and probe results: research.md R1, R2.

**Dev dependencies**: unchanged (ruff, mypy, pytest 8, testcontainers 4, pytest-randomly,
pytest-socket, jsonschema).

**Storage**: PostgreSQL 17.6 (`postgres:17.6-alpine`), revisions `0002_contract_run_metadata`
and `0003_contract_constraints` on top of `0001_initial_schema`.

**Testing**: pytest unit (pure, sockets disabled) + integration (testcontainers PostgreSQL, real
migrations), unchanged infrastructure. New shared case table `tests/contract_cases.py` drives both
pipeline and database parity tests.

**Target Platform**: macOS (dev), Linux containers / GitHub Actions (CI), unchanged.

**Project Type**: single Python project (CLI + library), `src/` layout.

**Performance Goals**: full real-data run still < 5 min (SC-005; 001 measured 18.7 s). Contract
evaluation measured at ≤ 0.53 s worst case on 240k rows (R15). Measured manually (T036).

**Constraints**: all-or-nothing load (FR-010) — every new write is inside the existing transaction;
pure functions take the run date as an argument (R5); every DB constraint has a pipeline rule that
excludes the row first (FR-007 parity); every source row still accounted for (SC-002).

**Scale/Scope**: 238,859 rows; planning profile predicts 1,381 contract violations (0.58 %),
largest rule `max_odds_ge_avg` at 0.57 % (R16). 9 rules, 17 input columns.

No `NEEDS CLARIFICATION` items remain.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / standard | How this plan complies | Evidence (test / artifact) |
|---|---|---|
| **I. No silent data loss** — every row loaded or recorded | Contract violators become `excluded_rows` with `contract_violation` and every violated rule id; convergence deletions of matches are always paired with the row's new exclusion record and counted per run (`matches_removed`) | `test_contract_fixture_loads_only_consistent_rows`, `test_rerun_removes_now_violating_matches`, `test_partition_accounts_for_every_row` |
| I. Explicit schema (data contract) before clean layers | The contract is a pandera `DataFrameSchema` (declared columns, dtypes and rules) evaluated before any DB write; pandera cannot be silently disabled (forced `config_context`, in-process and via env vars) and reports every failure | `test_find_violations_ignores_disabled_pandera_config`, `test_find_violations_ignores_pandera_env_disable`, `test_find_violations_is_complete_for_many_failures`, `test_schema_declares_rule_inputs_with_parser_dtypes` |
| I. Row counts reconcile and are logged | Unchanged invariants + per-rule counts logged and stored; `contract_violation` included in `excluded_by_reason` | `test_violations_by_rule_counts_each_rule_once_per_row`, `test_ingest_report_counts`, `test_db_reconciles_with_run_counts` |
| **II. Idempotent pipelines** | Same file twice → identical tables including ids; contract change + re-run → converges to fresh build | `test_second_run_leaves_identical_state`, `test_converged_rerun_is_noop`, `test_upgrade_then_rerun_equals_fresh_build` |
| II. Raw data kept unchanged | No change (001 R13) | 001 tests |
| II. Safe to retry after partial failure | All new statements in the single transaction; failure after the convergence deletes rolls everything back | `test_failure_after_convergence_rolls_back` |
| **III. Unit tests for cleaning/validation logic** | Contract rules, evaluation, precedence and counting are pure and unit-tested | `tests/unit/test_data_contract.py`, `test_validate_contract.py` |
| III. Integration tests on real PostgreSQL | Migrations, CHECK constraints, parity, convergence against testcontainers PostgreSQL 17.6 | `tests/integration/test_constraints.py`, `test_convergence.py` |
| III. API tests | N/A (no API) | — |
| III. Done only when tests pass in CI | CI runs `scripts/check.sh` (unchanged) + image build + smoke | `.github/workflows/ci.yml` |
| **IV. One-command stack** | Unchanged; `migrate` service applies 0002 and 0003 | `scripts/smoke_compose.sh` |
| IV. Pinned versions | pandera pinned `<0.35`, exact version in `uv.lock`; probed behaviours pinned by a test | `test_pandera_polars_backend_behaviour` |
| **V. Simple, incremental** | One new dependency, mandated by the spec and PLAN.md; no new services; contract evaluation is one vectorized pass; two small migrations, one per story | research.md R1, R15, R18 |
| Tech: versioned migrations, PKs/FKs/indexes | Schema only via Alembic 0002/0003; no new tables; orphan deletes use existing indexes; the stale-match delete is a measured sequential scan (no index on `source_sha256`), with `ANALYZE` on the temp tables (R15) | `test_upgrade_creates_schema`, `test_upgrade_is_noop_at_head` |
| Quality: typed code | pandera ships `py.typed`; probe passes `mypy --strict` | gate step 3 |
| Quality: pure logic, separate I/O | `data_contract.py` and `validate.py` take data + run date, return data; no clock, DB, network or files | unit tests run with sockets disabled; no clock calls in pure modules (R5) |
| Quality: one module per stage, no cycles | New leaf-ish module `data_contract` (imports `source_contract`, `errors`, polars, pandera); `validate → data_contract`; `pipeline → data_contract` | import graph below |
| Quality: explicit error handling | New `ContractSchemaError` (exit 9) for contract/frame mismatches, never converted to exclusions; new `ClockSkewError` (exit 10) when the DB date is behind the run date, raised before any write | `test_find_violations_wrong_dtype_raises_contract_schema_error`, `test_find_violations_raising_rule_raises_contract_schema_error`, `test_contract_schema_error_exit_code`, `test_clock_skew_error_exit_code`, `test_db_clock_behind_run_date_fails_before_writes` |
| Quality: docstrings, logging | Per-rule counts and convergence counts logged via `logging` | `caplog` assertions in `test_run_report_and_record_contract_metadata`, `test_rerun_removes_now_violating_matches` |
| Workflow: measured performance | SC-005 timed on the real dataset, recorded in README (T036) | quickstart §3 |
| Workflow: README current | Contract section + measured per-rule table, guarded by a test for the rule list | `test_readme_documents_every_rule` |

**Result (pre-design)**: PASS. **Result (post-design re-check)**: PASS. The design adds one
dependency, which the spec mandates, one module, two migrations and one error class, each tied to an
FR (see research.md). Complexity Tracking lists the items a reviewer may question.

## Deterministic Quality Gate

`scripts/check.sh` **already exists and is not changed** by this feature. It runs, in order, exiting
non-zero on the first failure:

1. `uv run --frozen ruff format --check .`
2. `uv run --frozen ruff check .`
3. `uv run --frozen mypy src tests` (strict)
4. `uv run --frozen pytest tests/unit --randomly-seed=20261011 --disable-socket`
5. `uv run --frozen pytest tests/integration --randomly-seed=20261011` (real PostgreSQL 17.6 via
   testcontainers)

Every task in tasks.md ends with the gate green, except test-writing tasks, whose new tests are
expected to fail until the paired implementation task. The first task adds pandera and keeps the
gate green (no skeleton task: the gate exists since 001 T001).

Determinism rules for this feature (in addition to 001's):
- **No clock in pure code**: `run_date` is an argument (R5). Unit tests use
  `tests.stages.RUN_DATE = dt.date(2021, 6, 30)`. Integration tests that ingest
  `matches_contract.csv` or test date boundaries pass `started_at=CONTRACT_STARTED_AT`
  (`dt.datetime(2021, 6, 30, 12, 0, tzinfo=dt.UTC)`, defined in `tests/stages.py`). Both are years
  in the past, so no test depends on the host's or the database container's real clock (plan
  review P1-2). Fixture "future" rows use `2999-01-01`.
- **Database clock**: the only `now()`-dependent assertions (`date_run_date`/`date_after_run_date`
  parity cases) read the database's own UTC date first and use it as the run date within the same
  transaction, so they cannot straddle midnight. The clock-skew test uses a run date of
  `2999-01-01`, which is later than any real database date.
- **No network**: all new data are fixtures under `tests/fixtures/`; the real-data measurement is
  a manual task (T036), not part of the suite.
- **Order independence**: every integration test uses its own database (template clone or
  `empty_db_url`); convergence tests build both databases inside the test.
- **pandera behaviour pinned**: `test_pandera_polars_backend_behaviour` fails if an upgrade changes
  failure-case shape, naming, row positions or config override.

## FR / SC → Test Mapping

Unit tests in `tests/unit/`, integration tests in `tests/integration/`. Names are exact and are
referenced by tasks.md. `[case]` = parametrized over `tests/contract_cases.py::CONTRACT_CASES`.

| Req | What is verified | Test(s) (file :: name) |
|---|---|---|
| FR-001 | single declarative, versioned contract; stable ids; descriptions; declared inputs/dtypes | `unit/test_data_contract.py::test_contract_rule_ids_stable`, `::test_schema_declares_rule_inputs_with_parser_dtypes`, `::test_pre_contract_is_version_zero_without_rules` |
| FR-002 | each rule detects its violation; skipped when inputs missing (per term); inclusive boundaries; run-date bound | `unit/test_data_contract.py::test_contract_case_outcome[case]`, `::test_date_in_range_uses_injected_run_date`; `unit/test_validate_contract.py::test_contract_fixture_outcomes_match_expected`; `integration/test_contract_pipeline.py::test_date_in_range_boundary_uses_run_start_date` |
| FR-003 | contract only on rows that passed 001 checks; before duplicates; before any DB write | `unit/test_validate_contract.py::test_per_column_failure_takes_precedence_over_contract`, `::test_same_team_takes_precedence_over_contract`, `::test_contract_checked_before_duplicates`, `::test_invalid_later_copy_is_contract_violation_not_duplicate`; `unit/test_source_contract.py::test_exclusion_reasons_in_precedence_order`; `integration/test_ingest_pipeline.py::test_missing_columns_stops_before_db` (001, still green) |
| FR-004 | violators not loaded; reason `contract_violation`; detail lists every rule in contract order | `unit/test_validate_contract.py::test_multi_rule_row_detail_lists_all_rules_in_contract_order`, `::test_contract_fixture_outcomes_match_expected`; `unit/test_data_contract.py::test_find_violations_sorted_long_format`; `integration/test_contract_pipeline.py::test_contract_fixture_loads_only_consistent_rows`; `integration/test_constraints.py::test_excluded_rows_reason_check` |
| FR-005 | per-rule counts (zeros included) in report, stored record and logs; read = loaded + excluded | `unit/test_accounting.py::test_violations_by_rule_counts_each_rule_once_per_row`, `::test_report_counts_per_reason`, `::test_report_invariants_hold[*]`; `integration/test_contract_pipeline.py::test_run_report_and_record_contract_metadata` (incl. `caplog` per-rule lines); `integration/test_cli.py::test_cli_ingest_report_matches_schema` (002 schema + invariants); `integration/test_ingest_pipeline.py::test_ingest_report_counts` |
| FR-006 | contract version stored per run; pre-contract runs back-filled with version 0 | `integration/test_contract_pipeline.py::test_run_report_and_record_contract_metadata`; `integration/test_migrations.py::test_upgrade_backfills_existing_runs`; `integration/test_constraints.py::test_ingest_runs_contract_columns_checks` |
| FR-007 | DB rejects new/updated violators for every rule; rule ↔ constraint parity (names and per-constraint behaviour); bad data never fails a run; clock disagreement fails before writes, not mid-load | `integration/test_constraints.py::test_db_agrees_with_pipeline_on_contract_case[case]`, `::test_db_constraints_agree_with_pipeline_on_fixture_rows[*]`, `::test_contract_constraint_rejects_violating_update`; `integration/test_atomicity.py::test_db_clock_behind_run_date_fails_before_writes`; `integration/test_migrations.py::test_every_contract_rule_has_db_constraint`; `integration/test_contract_pipeline.py::test_upsert_rewrites_existing_violating_match` |
| FR-008 | migration succeeds on a DB with violating matches; re-apply is a no-op; downgrade never drops exclusion records | `integration/test_migrations.py::test_upgrade_succeeds_with_violating_matches`, `::test_upgrade_is_noop_at_head`, `::test_downgrade_then_upgrade_roundtrip`, `::test_downgrade_refused_with_contract_violation_rows` |
| FR-009 | convergence: violators removed and recorded; stale exclusions removed; equals fresh build | `integration/test_convergence.py::test_upgrade_then_rerun_equals_fresh_build[*]`, `::test_rerun_removes_now_violating_matches`, `::test_rerun_loads_rows_whose_exclusion_no_longer_applies`, `::test_orphan_team_and_league_removed`, `::test_converged_rerun_is_noop`, `::test_convergence_scoped_to_source_file`, `::test_reload_after_loosened_contract_equals_fresh_build` |
| FR-010 | all-or-nothing incl. convergence deletes | `integration/test_atomicity.py::test_failure_after_convergence_rolls_back` (with positive control), `::test_db_clock_behind_run_date_fails_before_writes`, `::test_db_failure_mid_load_rolls_back` (001), `::test_concurrent_run_rejected` (001) |
| FR-011 | per-rule measurement on real data; > 1 % rules reviewed | **Manual** — T036 (quickstart §3), outcome in `decisions.md`; planning profile in research.md R16 |
| FR-012 | README documents contract version, rules and measured per-rule table | `unit/test_readme_contract.py::test_readme_documents_every_rule` (rule list and version); measured numbers: **manual** T036 |
| SC-001 | 0 loaded matches violate any rule | `integration/test_contract_pipeline.py::test_no_loaded_match_violates_contract` (`VALIDATE CONSTRAINT` on all nine inside a rolled-back transaction after ingest); `unit/test_validate_contract.py::test_accepted_rows_satisfy_contract[*]`; after convergence: `integration/test_convergence.py::test_upgrade_then_rerun_equals_fresh_build` (also validates); real data: quickstart §2 |
| SC-002 | loaded + excluded = read after every run | `unit/test_accounting.py::test_partition_accounts_for_every_row` (incl. `matches_contract.csv`); `integration/test_excluded_rows.py::test_db_reconciles_with_run_counts` (001, + `matches_contract.csv`); `integration/test_contract_pipeline.py::test_contract_fixture_loads_only_consistent_rows` |
| SC-003 | every rule enforced by the DB | `integration/test_constraints.py::test_db_agrees_with_pipeline_on_contract_case[case]` (every rule has ≥ 1 rejecting case — asserted by `::test_contract_cases_cover_every_rule`); `integration/test_migrations.py::test_every_contract_rule_has_db_constraint` |
| SC-004 | upgrade + re-run = fresh build (0 differences, full contents) | `integration/test_convergence.py::test_upgrade_then_rerun_equals_fresh_build[matches_contract.csv, matches_edge_cases.csv, matches_real_sample.csv]` (compares `helpers.content_snapshot`, research.md R12) |
| SC-005 | full real run < 5 min, measured | **Manual** — T036 (quickstart §3), README |
| SC-006 | README per-rule counts from an actual run | **Manual** — T036; rule list guarded by `test_readme_documents_every_rule` |
| US1-AS4 | missing optional statistics → rules skipped, row loaded | `unit/test_data_contract.py::test_contract_case_outcome[ht_result_missing_goals, ht_partial, sot_one_side_missing, odds_all_missing]`; fixture row 14/15 in `test_contract_fixture_outcomes_match_expected` |
| US2-AS3 | re-apply at latest version → no change | `integration/test_migrations.py::test_upgrade_is_noop_at_head` |
| US3-AS3 | converged re-run changes nothing (ids included) | `integration/test_convergence.py::test_converged_rerun_is_noop`; `integration/test_idempotency.py::test_second_run_leaves_identical_state[*]` (+ `matches_contract.csv`) |
| US4-AS1 | any run shows version and per-rule counts | `integration/test_contract_pipeline.py::test_run_report_and_record_contract_metadata` |
| Design: pandera pinned | failure-case shape / naming / positions / config override | `unit/test_pandera_backend.py::test_pandera_polars_backend_behaviour` |
| Design: R9 upsert safety | UPDATE of an existing violating row by a valid copy succeeds | `integration/test_contract_pipeline.py::test_upsert_rewrites_existing_violating_match` |
| Design: exit 9 | `ContractSchemaError` code and name; dtype mismatch and raising rule | `unit/test_errors.py::test_contract_schema_error_exit_code`; `unit/test_data_contract.py::test_find_violations_wrong_dtype_raises_contract_schema_error`, `::test_find_violations_raising_rule_raises_contract_schema_error` |
| Design: exit 10 | `ClockSkewError` code and name; raised before any write | `unit/test_errors.py::test_clock_skew_error_exit_code`; `integration/test_atomicity.py::test_db_clock_behind_run_date_fails_before_writes` |
| Design: pandera completeness and env override | env vars set before import cannot disable the contract; no cap on failures | `unit/test_data_contract.py::test_find_violations_ignores_pandera_env_disable`, `::test_find_violations_is_complete_for_many_failures` |

## Test Fixtures and Shared Test Data

All fixtures use the real 48-column header. Base row **B** = data row 1 of
`tests/fixtures/matches_valid.csv` (`E0,…,Arsenal,Chelsea,…,FT 2.0,1.0,H, HT 1.0,0.0,H, shots
14.0/9.0, targets 6.0/3.0, odds 1.65/3.9/5.25, max 1.7/4.1/5.6, …`).

### `tests/fixtures/matches_contract.csv` + `matches_contract.expected.json` (new)

Each row is B with the listed overrides. Rows 1–16 use `MatchDate` `2020-10-NN` (NN = row number,
zero-padded) unless overridden. Expected outcomes are with contract version 1 and hold for any
run date from 2020-11-04 to 2998-12-31; tests use 2021-06-30. "pre" = outcome under `PRE_CONTRACT` (used by the convergence tests).

| Row | Overrides | v1 outcome (detail) | pre outcome |
|---|---|---|---|
| 1 | — | loaded | loaded |
| 2 | `HTHome` 3.0 | `ht_le_ft_home` | loaded |
| 3 | `HTHome` 0.0, `HTAway` 2.0, `HTResult` A | `ht_le_ft_away` | loaded |
| 4 | `FTResult` A | `ft_result_consistent` | loaded |
| 5 | `HTResult` D | `ht_result_consistent` | loaded |
| 6 | `HomeTarget` 15.0 | `sot_le_shots_home` | loaded |
| 7 | `AwayTarget` 10.0 | `sot_le_shots_away` | loaded |
| 8 | `OddDraw` 0.0 | `odds_min` | loaded |
| 9 | `OddHome` 0.95, `MaxHome` empty | `odds_min` | loaded |
| 10 | `MaxAway` 5.0 | `max_odds_ge_avg` | loaded |
| 11 | `MatchDate` 1999-12-31 | `date_in_range` | loaded |
| 12 | `MatchDate` 2999-01-01 | `date_in_range` | loaded |
| 13 | `FTHome` 0.0, `FTAway` 2.0 (`FTResult` H, HT 1-0 H kept) | `ht_le_ft_home, ft_result_consistent` | loaded |
| 14 | `HTHome`, `HTAway`, `HTResult`, `HomeShots`, `AwayShots`, `HomeTarget`, `AwayTarget`, `OddHome`, `OddDraw`, `OddAway`, `MaxHome`, `MaxDraw`, `MaxAway` all empty | loaded | loaded |
| 15 | `HomeTarget` empty, `AwayTarget` 9.0 | loaded | loaded |
| 16 | `MatchDate` 2000-01-01, `HTHome` 2.0, `HTAway` 1.0, `HomeTarget` 14.0, `OddHome` 1.0, `MaxHome` 1.0, `MaxDraw` 3.9 | loaded | loaded |
| 17 | `MatchDate` 2020-11-01, `HomeTeam` Leeds, `AwayTeam` Wolves, `FTResult` D | `ft_result_consistent` | loaded |
| 18 | `MatchDate` 2020-11-01, `HomeTeam` ` leeds `, `AwayTeam` `WOLVES` | loaded (not a duplicate: row 17 was excluded first) | `duplicate of source row 17` |
| 19 | identical to row 1 | `duplicate of source row 1` | `duplicate of source row 1` |
| 20 | row 1's key (`MatchDate` 2020-10-01), `AwayTarget` 10.0 | `sot_le_shots_away` (contract before duplicate) | `duplicate of source row 1` |
| 21 | `MatchDate` 2020-11-02, `HomeShots` -1, `FTResult` A | `invalid_value` `HomeShots` (001 reason wins) | same |
| 22 | `Division` X1, `MatchDate` 2020-11-03, `HomeTeam` Ghost FC, `AwayTeam` Arsenal, `HomeTarget` 15.0 | `sot_le_shots_home` (only row with league X1 and team Ghost FC) | loaded |
| 23 | `MatchDate` 2020-11-04, `HomeTeam` Arsenal, `AwayTeam` ` arsenal`, `FTResult` A | `same_team` `arsenal` (001 reason wins) | same |

Derived expectations (asserted by tests): v1 fresh run — read 23, loaded 5 (rows 1, 14, 15, 16,
18), excluded 18 (`contract_violation` 15, `duplicate` 1, `invalid_value` 1, `same_team` 1);
`violations_by_rule` = `ht_le_ft_home` 2, `ht_le_ft_away` 1, `ft_result_consistent` 3,
`ht_result_consistent` 1, `sot_le_shots_home` 2, `sot_le_shots_away` 2, `odds_min` 2,
`max_odds_ge_avg` 1, `date_in_range` 2 (sum 16 = 15 rows + 1 two-rule row); leagues `{E0}`; teams
`{arsenal, chelsea, leeds, wolves}` with names `Arsenal, Chelsea, leeds, WOLVES`. Pre-contract
state (seeded at 0001) → upgrade → v1 re-run: `matches_inserted` 0, `matches_updated` 1 (key of
row 17 rewritten with row 18's values and provenance), `matches_unchanged` 4, `matches_removed` 13
(rows 2–13, 22), `excluded_rows_removed` 1 (row 18's `duplicate` record); team `ghost fc` and league
`X1` removed; team names `Leeds`/`Wolves` become `leeds`/`WOLVES`.

### `tests/fixtures/matches_edge_cases.csv` (repaired, research.md R17)

Rows 19 and 21 (file lines 20 and 22): `HTHome,HTAway,HTResult` `1.0,0.0,H` → `0.0,1.0,A`.
`matches_edge_cases.expected.json` is unchanged.

### `tests/contract_cases.py` (new, shared by unit and integration tests)

`FULL_ROW: dict[str, str]` = base row B as source strings (all 48 columns). `ContractCase(case_id:
str, overrides: dict[str, str | None], violated: tuple[str, ...])` (frozen dataclass);
`CONTRACT_CASES: tuple[ContractCase, ...]`; `materialize(case, run_date) -> dict[str, str | None]`
returns `{**FULL_ROW, **overrides}` with `MatchDate` placeholders `{run_date}` and
`{run_date+1}` replaced by ISO dates. Cases (`violated` in contract order):

| case_id | overrides | violated |
|---|---|---|
| `valid_full` | — | — |
| `ht_home_gt_ft` | `HTHome` 3.0 | `ht_le_ft_home` |
| `ht_away_gt_ft` | `HTHome` 0.0, `HTAway` 2.0, `HTResult` A | `ht_le_ft_away` |
| `ht_eq_ft` | `HTHome` 2.0, `HTAway` 1.0 | — |
| `ft_result_wrong` | `FTResult` A | `ft_result_consistent` |
| `ft_result_draw_wrong` | `FTHome` 1.0, `FTAway` 1.0, `FTResult` H | `ft_result_consistent` |
| `ht_result_wrong` | `HTResult` D | `ht_result_consistent` |
| `ht_result_missing_goals` | `HTHome` None, `HTAway` None, `HTResult` H | — |
| `ht_partial` | `HTAway` None, `HTResult` H | — |
| `sot_gt_shots_home` | `HomeTarget` 15.0 | `sot_le_shots_home` |
| `sot_gt_shots_away` | `AwayTarget` 10.0 | `sot_le_shots_away` |
| `sot_eq_shots` | `HomeTarget` 14.0, `AwayTarget` 9.0 | — |
| `sot_one_side_missing` | `HomeShots` None, `HomeTarget` 30.0 | — |
| `odd_below_one` | `OddDraw` 0.99 | `odds_min` |
| `odd_zero_max_missing` | `OddHome` 0.0, `MaxHome` None | `odds_min` |
| `max_odd_below_one` | `MaxAway` 0.5 | `odds_min`, `max_odds_ge_avg` |
| `odd_exactly_one` | `OddHome` 1.0, `MaxHome` 1.0 | — |
| `max_below_avg` | `MaxHome` 1.6 | `max_odds_ge_avg` |
| `odds_all_missing` | `OddHome`, `OddDraw`, `OddAway`, `MaxHome`, `MaxDraw`, `MaxAway` None | — |
| `date_before_2000` | `MatchDate` 1999-12-31 | `date_in_range` |
| `date_2000_01_01` | `MatchDate` 2000-01-01 | — |
| `date_run_date` | `MatchDate` `{run_date}` | — |
| `date_after_run_date` | `MatchDate` `{run_date+1}` | `date_in_range` |
| `ht_result_missing_with_goals` | `HTResult` None (HT goals 1-0 present) | — |
| `avg_missing_max_present` | `OddHome` None (`MaxHome` 1.7 present) | — |
| `draw_only_max_below_avg` | `OddHome`, `OddAway`, `MaxHome`, `MaxAway` None, `MaxDraw` 3.5 | `max_odds_ge_avg` |
| `multi_rule` | `FTHome` 0.0, `FTAway` 2.0 | `ht_le_ft_home`, `ft_result_consistent` |

(27 cases; all 27 verified at planning time against the R3 expressions.)

## Project Structure

### Documentation (this feature)

```text
specs/002-data-contract/
├── plan.md              # This file
├── research.md          # Phase 0: decisions R1–R19
├── data-model.md        # Phase 1: schema deltas, contract entities, parity, load sequence
├── quickstart.md        # Phase 1: manual acceptance and real-data measurement
├── contracts/
│   ├── data-contract.md          # the rules, semantics, versioning policy
│   ├── run-report.schema.json    # ingest report v2 (supersedes 001's for ingest)
│   └── cli.md                    # CLI deltas: report fields, exit 9, migrations
├── decisions.md         # review responses + FR-011 review outcome (created by T036 / reviews)
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root; new = +, changed = ~)

```text
pyproject.toml, uv.lock                ~ add pandera[polars]
migrations/versions/
├── 0001_initial_schema.py             (unchanged)
├── 0002_contract_run_metadata.py      + ingest_runs columns, reason CHECK
└── 0003_contract_constraints.py       + nine NOT VALID CHECKs on matches
src/football_stats/
├── errors.py                          ~ ContractSchemaError (exit 9), ClockSkewError (exit 10)
├── source_contract.py                 ~ ExclusionReason.CONTRACT_VIOLATION
├── data_contract.py                   + rules, DataContract, build_schema, find_violations [pure]
├── validate.py                        ~ partition(parsed, *, contract, run_date); Partition.violations
├── load.py                            ~ RunMeta/LoadCounts fields; convergence deletes; orphans
├── pipeline.py                        ~ run(..., *, contract, started_at); report fields; logs
└── (clean, extract, cli, config, verify unchanged)
tests/
├── stages.py                          ~ RUN_DATE, CONTRACT_STARTED_AT, contract/run_date kwargs
├── contract_cases.py                  + shared case table
├── fixtures/                          + matches_contract.csv/.expected.json; ~ edge_cases rows 19, 21; ~ SOURCES.txt
├── unit/  + test_pandera_backend.py, test_data_contract.py, test_validate_contract.py,
│            test_errors.py, test_readme_contract.py; ~ test_accounting.py, test_source_contract.py
└── integration/
    ├── conftest.py                    ~ make_settings fixture
    ├── helpers.py                     ~ snapshot(ingest_runs cols), content_snapshot, seed_pre_contract
    ├── test_contract_pipeline.py      +
    ├── test_convergence.py            +
    └── test_migrations.py, test_constraints.py, test_atomicity.py, test_idempotency.py,
        test_cli.py, test_ingest_pipeline.py, test_excluded_rows.py   ~
README.md                              ~ data contract section, measured per-rule table
```

Import graph (no cycles): `cli → pipeline, verify, config, errors`; `pipeline → extract, clean,
validate, load, data_contract, errors`; `validate → data_contract, source_contract, errors`;
`data_contract → source_contract, errors` (+ polars, pandera); `load → source_contract, errors`
(+ psycopg); `clean → source_contract`.

**Structure Decision**: unchanged single-project `src/` layout. The contract gets its own module
(`data_contract.py`) because it is a separate concern from the 48-column source description
(`source_contract.py`) and is the unit that is versioned.

## Complexity Tracking

No constitution violations. Items a reviewer might question:

| Item | Why needed | Simpler alternative rejected because |
|---|---|---|
| pandera dependency (+ pydantic, typeguard transitively) | Spec and PLAN.md mandate a declarative pandera contract (constitution I "explicit schema") | hand-written polars rules would ignore the mandated technology (research.md R1) |
| Two migrations instead of one | US1 (P1) can ship without DB enforcement; constraint tests are written before `0003` exists | one revision would force constraint code ahead of its tests or merge two stories (R18) |
| `NOT VALID` constraints never validated by the app | FR-008: upgrade must succeed with violators present; other files' violators are out of convergence scope | validated constraints fail the migration; app-issued DDL breaks 001's "migrations only" rule (R8) |
| Orphan team/league deletion | Without it an upgraded DB differs from a fresh build (SC-004) | keeping orphans fails `test_upgrade_then_rerun_equals_fresh_build[matches_contract.csv]` (R11) |
| `ContractSchemaError` exit 9 | Distinguishes a contract/parser bug from bad data; never converted into exclusions | exit 1 hides the cause; converting to exclusions would "succeed" while excluding everything (R14) |
| `ClockSkewError` exit 10 + one `SELECT` before writes | App and DB clocks can disagree; without the check a skew surfaces as an anonymous CheckViolation mid-load (R8) | trusting one clock is not guaranteed (Docker VM drift, remote DB); exit 7 would hide the remedy |
| Seeding helper that writes a 0001-era DB with plain SQL | Only way to obtain violating matches under constraints for FR-008/SC-004 tests | test-only bypass flags in production `load.py` (R13) |
