# Data Model: Match Data Contract

**Feature**: `002-data-contract` | **Date**: 2026-10-15 | **Plan**: [plan.md](./plan.md)

This feature **extends** feature 001's schema
([001 data-model.md](../001-match-data-ingestion/data-model.md)); everything not listed here is
unchanged. Schema changes are made by two Alembic revisions:
`0002_contract_run_metadata` (`down_revision = "0001_initial_schema"`; sections 1.2, 1.3) and
`0003_contract_constraints` (`down_revision = "0002_contract_run_metadata"`; section 1.1). They are
separate so the pipeline contract (User Story 1) can ship before database enforcement (User Story 2). Decisions cited as R# are in
[research.md](./research.md).

## 1. Schema changes

### 1.1 `matches`: nine contract constraints (FR-007, R3, R8)

All added with `ALTER TABLE matches ADD CONSTRAINT <name> CHECK (<expr>) NOT VALID`. They are
enforced for every new or updated row; existing rows are not scanned (FR-008). A NULL input makes the
comparison NULL, which a CHECK accepts (rule skipped, FR-002).

| Constraint name | CHECK expression (exact) |
|---|---|
| `matches_contract_ht_le_ft_home` | `ht_home_goals <= ft_home_goals` |
| `matches_contract_ht_le_ft_away` | `ht_away_goals <= ft_away_goals` |
| `matches_contract_ft_result_consistent` | `ft_result = CASE WHEN ft_home_goals > ft_away_goals THEN 'H' WHEN ft_home_goals = ft_away_goals THEN 'D' WHEN ft_home_goals < ft_away_goals THEN 'A' END` |
| `matches_contract_ht_result_consistent` | `ht_result = CASE WHEN ht_home_goals > ht_away_goals THEN 'H' WHEN ht_home_goals = ht_away_goals THEN 'D' WHEN ht_home_goals < ht_away_goals THEN 'A' END` |
| `matches_contract_sot_le_shots_home` | `home_shots_on_target <= home_shots` |
| `matches_contract_sot_le_shots_away` | `away_shots_on_target <= away_shots` |
| `matches_contract_odds_min` | `odd_home >= 1.0 AND odd_draw >= 1.0 AND odd_away >= 1.0 AND max_odd_home >= 1.0 AND max_odd_draw >= 1.0 AND max_odd_away >= 1.0` |
| `matches_contract_max_odds_ge_avg` | `max_odd_home >= odd_home AND max_odd_draw >= odd_draw AND max_odd_away >= odd_away` |
| `matches_contract_date_in_range` | `match_date >= DATE '2000-01-01' AND match_date <= (now() AT TIME ZONE 'UTC')::date` |

No column of `matches` changes; `test_source_contract_matches_db_columns` (001) stays valid.

### 1.2 `ingest_runs`: four new columns (FR-005, FR-006, R7)

| Column | Type | Null | Constraint | Back-fill for existing rows |
|---|---|---|---|---|
| `contract_version` | `integer` | NO | `CHECK (contract_version >= 0)` | `0` (= `PRE_CONTRACT`, feature 001 rules only) |
| `violations_by_rule` | `jsonb` | NO | `CHECK (jsonb_typeof(violations_by_rule) = 'object')` | `'{}'` |
| `matches_removed` | `integer` | NO | `CHECK (matches_removed >= 0)` | `0` |
| `excluded_rows_removed` | `integer` | NO | `CHECK (excluded_rows_removed >= 0)` | `0` |

Each is added as `ADD COLUMN … NOT NULL DEFAULT <back-fill> CHECK (…)`, then `ALTER COLUMN …
DROP DEFAULT` in the same migration, so the application must always write all four.

`violations_by_rule` holds `{rule_id: count}` with **every** rule id of the run's contract version
(nine keys for version 1), zeros included. The existing checks
`rows_read = rows_loaded + rows_excluded` and `rows_loaded = matches_inserted + matches_updated +
matches_unchanged` are unchanged: removed matches are not part of this run's loaded rows.

### 1.3 `excluded_rows`: new reason code (FR-004)

`excluded_rows_reason_check` is dropped and re-created as
`CHECK (reason IN ('missing_field', 'invalid_date', 'missing_result', 'invalid_value', 'same_team',
'contract_violation', 'duplicate'))`. `excluded_by_reason` in `ingest_runs` and in the report
always has all seven keys.

### 1.4 Downgrade (R18)

`0003` drops the nine constraints. `0002` drops the four columns and restores the six-code reason
CHECK (validated; that revision fails and is rolled back while `contract_violation` rows exist).
Because `env.py` commits each revision separately (`transaction_per_migration=True`), a downgrade
from head to `0001` then stops at `0002_contract_run_metadata` (R18).

## 2. The data contract (FR-001, FR-002)

Single definition: `src/football_stats/data_contract.py` (pure; imports `polars`, `pandera.polars`,
`source_contract`, `errors`). Human-readable version: [contracts/data-contract.md](./contracts/data-contract.md).

| Entity | Fields | Notes |
|---|---|---|
| `Term` | `inputs: tuple[str, ...]` (db column names), `condition: Callable[[dt.date], pl.Expr]` | skipped when any input is null (R3) |
| `ContractRule` | `rule_id: str` (stable, `^[a-z][a-z0-9_]*$`, ≤ 46 chars so `matches_contract_<id>` fits PostgreSQL's 63-char limit), `description: str`, `terms: tuple[Term, ...]`; property `inputs` (union, first-seen order); method `passes(run_date) -> pl.Expr` (never null) | |
| `DataContract` | `version: int`, `rules: tuple[ContractRule, ...]`; property `rule_ids` | rule order = contract order (detail and report order) |
| constants | `CONTRACT_VERSION = 1`, `CURRENT_CONTRACT = DataContract(1, RULES)`, `PRE_CONTRACT = DataContract(0, ())` | R6 |

Rules of version 1, in contract order: `ht_le_ft_home`, `ht_le_ft_away`, `ft_result_consistent`,
`ht_result_consistent`, `sot_le_shots_home`, `sot_le_shots_away`, `odds_min`, `max_odds_ge_avg`,
`date_in_range`. Terms and conditions: research.md R3.

Functions (pure):
- `build_schema(contract: DataContract, run_date: dt.date) -> pa.DataFrameSchema` — one
  `pa.Column` per input column with the dtype of its parser (`COUNT→Int16`, `DECIMAL→Float64`,
  `RESULT→String`, `DATE→Date`; nullable unless the column kind is key/required), and one
  DataFrame-level `pa.Check(name=rule_id)` per rule; `strict=False`, `coerce=False` (R2).
- `find_violations(frame: pl.DataFrame, contract: DataContract, run_date: dt.date) -> pl.DataFrame`
  — columns `source_row_number: UInt32`, `rule_id: String`; one row per (row, violated rule);
  sorted by `source_row_number`, then contract order; raises `ContractSchemaError` on any non-rule
  failure (R2, R14).

**Contract Violation** (spec entity) = one row of that frame. It is persisted only through the
excluded row's `detail` and counted in `violations_by_rule`.

## 3. Validation precedence (extends 001 R6; R4)

| # | Reason | Evaluated on | Detail |
|---|---|---|---|
| 1 | `missing_field` | all rows | 001 |
| 2 | `invalid_date` | all rows | 001 |
| 3 | `missing_result` | all rows | 001 |
| 4 | `invalid_value` | all rows | 001 |
| 5 | `same_team` | all rows | 001 |
| 6 | `contract_violation` | rows with no reason 1–5 | violated rule ids in contract order, joined by `", "` |
| 7 | `duplicate` | rows with no reason 1–6 | `duplicate of source row N` (lowest remaining row for the key) |

`validate.partition(parsed, *, contract: DataContract, run_date: dt.date) -> Partition`.
`Partition` gains `violations: pl.DataFrame` (the `find_violations` frame for this file) and
`rule_ids: tuple[str, ...]`, plus `violations_by_rule() -> dict[str, int]` (every rule id, zeros
included, contract order). `counts_by_reason()` returns all seven codes. The accounting invariant
(`accepted + excluded == rows_read`, `AccountingError`) is unchanged.

## 4. Constraint parity (FR-007)

Every database constraint that can reject a pipeline row has a pipeline rule that excludes the row
first. The 001 table (001 data-model §3) still holds; this feature adds:

| DB constraint | Pipeline rule (reason `contract_violation`, detail contains) | Unit test (pipeline) | Integration test (database) |
|---|---|---|---|
| `matches_contract_ht_le_ft_home` | `ht_le_ft_home` | `test_contract_case_outcome[ht_home_gt_ft]` | `test_db_agrees_with_pipeline_on_contract_case[ht_home_gt_ft]` |
| `matches_contract_ht_le_ft_away` | `ht_le_ft_away` | `…[ht_away_gt_ft]` | `…[ht_away_gt_ft]` |
| `matches_contract_ft_result_consistent` | `ft_result_consistent` | `…[ft_result_wrong]`, `…[ft_result_draw_wrong]` | same ids |
| `matches_contract_ht_result_consistent` | `ht_result_consistent` | `…[ht_result_wrong]` | same id |
| `matches_contract_sot_le_shots_home` | `sot_le_shots_home` | `…[sot_gt_shots_home]` | same id |
| `matches_contract_sot_le_shots_away` | `sot_le_shots_away` | `…[sot_gt_shots_away]` | same id |
| `matches_contract_odds_min` | `odds_min` | `…[odd_below_one]`, `…[odd_zero_max_missing]`, `…[max_odd_below_one]` | same ids |
| `matches_contract_max_odds_ge_avg` | `max_odds_ge_avg` | `…[max_below_avg]`, `…[max_odd_below_one]` | same ids |
| `matches_contract_date_in_range` | `date_in_range` (pipeline bound = run date ≤ DB bound, R8) | `…[date_before_2000]`, `…[date_after_run_date]` | same ids |
| all nine (skip semantics) | inputs missing → skipped | `…[ht_result_missing_goals]`, `…[ht_result_missing_with_goals]`, `…[ht_partial]`, `…[sot_one_side_missing]`, `…[odds_all_missing]`, `…[avg_missing_max_present]` | same ids (accepted) |
| `max_odds_ge_avg` per-outcome terms | draw term alone | `…[draw_only_max_below_avg]` | same id |
| all nine (boundaries) | `<=`/`>=` inclusive | `…[ht_eq_ft]`, `…[sot_eq_shots]`, `…[odd_exactly_one]`, `…[date_2000_01_01]`, `…[date_run_date]` | same ids (accepted) |
| name parity | rule ids == `matches_contract_*` names | `test_contract_rule_ids_stable` | `test_every_contract_rule_has_db_constraint` |
| differential on fixtures | every row of three fixtures, per constraint | — | `test_db_constraints_agree_with_pipeline_on_fixture_rows[*]` |

The integration parity tests evaluate **each constraint's expression separately**
(`pg_get_expr(conbin, conrelid)` on a constraint-free `LIKE matches` temp table), so the full set of
failing constraints is compared with the full set of violated rules, not just the first constraint
PostgreSQL reports (R13).

Case table: `tests/contract_cases.py` (plan.md "Test Fixtures"). `excluded_rows_reason_check` ↔
`ExclusionReason`: `test_excluded_rows_reason_check` (extended) and
`test_exclusion_reasons_in_precedence_order`.

## 5. Load sequence (extends 001 R11; R9, R10, R11)

All in the existing single transaction, under the advisory lock:

1. `pg_try_advisory_xact_lock` (001).
1a. **Clock check**: if `(now() AT TIME ZONE 'UTC')::date < RunMeta.run_date`, raise
   `ClockSkewError` (exit 10) before any write (R8).
2. Temp tables + COPY: `stg_matches`, `stg_teams` (001) **and** `stg_excluded` (moved here from
   step 10 so the convergence delete can use it); then `ANALYZE stg_matches, stg_excluded` (R15).
3. Leagues insert, teams upsert (001).
4. Team-resolution accounting check (001).
5. Matches upsert → inserted / updated / unchanged (001, unchanged SQL; R9 explains why it cannot
   trip a `NOT VALID` constraint).
6. **Converge matches**: delete matches of this `source_sha256` without a staged row of the same
   natural key and `source_row_number` → `matches_removed` (R10).
7. **Converge exclusions**: delete `excluded_rows` of this `source_sha256` whose row number is not
   staged → `excluded_rows_removed` (R10).
8. **Orphans**: delete teams and leagues referenced by no match; log counts (R11).
9. Insert `ingest_runs` with all counts incl. `contract_version`, `violations_by_rule`,
   `matches_removed`, `excluded_rows_removed`.
10. Upsert excluded rows (001 step 7, unchanged semantics: keep first `run_id`, rewrite
    reason/detail/raw_record when different).
11. Commit. Any error → rollback; database exactly as before (FR-010).

## 6. Row lifecycle across runs of the same file (FR-009, US3)

| Before this run (same file S) | This run's outcome | After this run |
|---|---|---|
| loaded as match | accepted | match kept (unchanged or updated) |
| loaded as match | excluded (e.g. `contract_violation`) | match deleted (step 6), excluded row inserted (step 10) |
| excluded | accepted | excluded row deleted (step 7), match inserted or key-row updated (step 5) |
| excluded (reason A) | excluded (reason B) | excluded row updated in place, `run_id` kept |
| loaded, key K, row r | row r excluded, its duplicate r' accepted (same K) | K-row updated to r' values and provenance (step 5); r recorded as excluded |
| — (other file S0) | — | untouched, even if it violates the contract (out of scope; allowed by `NOT VALID`) |

After the run: matches with provenance S = accepted rows; excluded rows with provenance S = this
run's exclusions; no team or league without a match. That equals a fresh build on an empty database
with the same contract and run date, compared as content snapshots (R12).
