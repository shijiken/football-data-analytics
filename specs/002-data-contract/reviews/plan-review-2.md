# Plan Review — 002-data-contract, round 2

**Reviewed commit**: f046362 (branch `002-data-contract`)
**Inputs**: the round-1 review, `decisions.md` (P1-1 … P1-10), and the full diff f313766..f046362 of
plan.md, research.md, data-model.md, contracts/*, quickstart.md and tasks.md.
**Checked against the real code**: `migrations/env.py`, `src/football_stats/errors.py`,
`tests/stages.py`, `tests/unit/test_accounting.py`, `tests/integration/test_verify.py`, and the
fixture files. I also re-ran the project's own parsers and `partition_file` on the three existing
fixtures to check the new test run date against them.

Verdict: REVISE

One new MAJOR finding: the round-2 change of the test run date breaks a feature-001 unit test
and makes a research fact false. There are also two new MINOR findings. Every round-1 finding is
resolved.

---

## Rulings on round-1 responses

| ID | Planner response | Ruling | Verification |
|---|---|---|---|
| P1-1 | FIXED | Resolved | R8 no longer advises a manual `VALIDATE`. Quickstart §2 labels it a read-only proof inside `BEGIN … ROLLBACK`. T026 and T029 run it rolled back, and T026 asserts `convalidated = false` afterwards. T034 forbids the advice and T037 greps for it. |
| P1-2 | FIXED | Resolved | (a) R8 now limits the claim to a shared clock. (b) `RunMeta.run_date` is added, and T021 checks it right after the lock and before any write. `ClockSkewError` takes exit 10, which `errors.py` does not use yet (codes 1–8, contract 9). It is in the CLI contract and the schema enum, and is tested by `test_db_clock_behind_run_date_fails_before_writes` and `test_clock_skew_error_exit_code`. (c) The boundary test now uses 2021 dates. **But see finding 1**: moving the shared `RUN_DATE` default has a side effect that was missed. |
| P1-3 | FIXED | Resolved | Each constraint is evaluated separately through `pg_get_expr(conbin, conrelid)` against a constraint-free `LIKE matches` probe table. The three missing NULL/draw cases are added (27 cases). The differential test over three fixtures is added. See finding 3 for an implementation detail. |
| P1-4 | FIXED | Resolved | Three tests are added to T011: the subprocess env-disable test, 50,000-row completeness, and a raising rule mapped to `ContractSchemaError`. They are backed by R2 facts 7 and 8. The raising-rule test reads a column outside the selected inputs, which matches fact 7's probe. |
| P1-5 | FIXED, deviating fix (test split instead of a factory fixture) | **ACCEPT** | Splitting was the second option I offered. `test_reload_after_loosened_contract_equals_fresh_build` uses exactly `db_url` and `empty_db_url` + `upgrade`, and both fixtures exist in `tests/integration/conftest.py`. A factory fixture for one test would be unjustified (Principle V). |
| P1-6 | FIXED | Resolved | The positive control (`matches_removed == 13`, `excluded_rows_removed == 1` on a second `load_run` over the same connection) makes T030 fail before T031 and pass after. Together with the injected failure at the last step, it shows the deletes ran and were rolled back. |
| P1-7 | FIXED, with the asserted outcome changed from my request | **ACCEPT** | I confirmed `migrations/env.py:30` sets `transaction_per_migration=True`. So `downgrade 0001` commits `0003`'s downgrade and only `0002`'s downgrade is refused and rolled back. My requested assertion ("`alembic_version` and catalog unchanged") would have failed against correct code. The new assertions are right: version `0002`, no `matches_contract_*`, 0002 columns and seven-code CHECK present, all 15 `contract_violation` rows kept. They match the corrected R18 and data-model §1.4. That end state is consistent and loses no rows (Principle I). Not changing `env.py` is the right call. |
| P1-8 | FIXED | Resolved | `caplog` assertions on the nine `contract rule=` lines (T015) and on the `converge …` line with 13/1/1/1 (T029). Those counts match the fixture: `ghost fc` and `X1` are the only orphans. |
| P1-9 | FIXED | Resolved | The claim is corrected in the Constitution Check, R11 and R15. `ANALYZE stg_matches, stg_excluded` is added to T031. Declining an index on `source_sha256` until the measurement in T036 is reasonable. |
| P1-10 | FIXED | Resolved | (a) T012 spells out `FULL_ROW`. (b) `test_report_invariants_hold[*]` and the CLI-report invariants are added. (c) The `TBD-T036` placeholder plus the T037 grep. |

---

## Findings

### 1. MAJOR — `RUN_DATE = 2021-06-30` turns a real-sample row into a contract violation and breaks an existing 001 test; the "no expected value changed" claim is wrong

**Location**: tasks.md T010 (`RUN_DATE = dt.date(2021, 6, 30)`, also the default `run_date` of
`partition_file`/`partition_rows`) and T013; decisions.md P1-2(c) ("no expected value changed");
research.md R5 and R17 ("`matches_real_sample.csv` (14 accepted) pass").

**Problem**: `tests/fixtures/matches_real_sample.csv` row 15
(`ARG,2022-02-07,…,Godoy Cruz,Colon Santa Fe`) is accepted by feature 001. I ran `partition_file`:
14 accepted rows, and row 15 has `match_date = 2022-02-07`. With the new default
`run_date = 2021-06-30`, that row becomes a `contract_violation` (`date_in_range`) in every unit
test that calls `partition_file` without an explicit date. The existing
`tests/unit/test_accounting.py::test_report_counts_per_reason` asserts
`sum(counts.values()) == part.excluded.height == 2` and `part.accepted.height == 14`. It would see
3 and 13. T013 only tells the implementer to make that test expect "all seven codes", so the gate
goes red at T022 with no plan-sanctioned fix. The implementer would then have to choose between
editing a 001 expectation and changing the date, which is the kind of silent drift the plan's
"stop and report" discipline is meant to prevent. The P1-3 claim of "0 mismatches" for the fixture
differential is still fine, because that test uses the database date. The contract fixture's
claimed validity window (≥ 2020-11-04) is also correct. The unit-level defaults are what was
missed.

**Required change**: Choose a fixed past test date on or after the latest accepted date in any
fixture. For example, set `RUN_DATE = dt.date(2023, 6, 30)` and
`CONTRACT_STARTED_AT = dt.datetime(2023, 6, 30, 12, 0, tzinfo=dt.UTC)`; the T015 boundary test
can keep its own explicit 2021 dates. State the constraint in R5 ("`RUN_DATE` ≥ max accepted
`MatchDate` of every fixture, currently 2022-02-07"). Add a unit guard such as
`test_run_date_not_before_fixture_dates`, asserting that `find_violations` with `RUN_DATE` reports
no `date_in_range` on the accepted rows of every existing fixture except `matches_contract.csv`
rows 11–12. Correct decisions.md P1-2(c) accordingly. Lowering 001's expectations instead is not
acceptable: it would make a real-data fixture row look invalid only because the test date is
artificial.

### 2. MINOR — Some date-range tests read the real clock in ways the plan's "no real clock" statement doesn't mention

**Location**: research.md R5 ("no test depends on the real clock"); T026
(`test_no_loaded_match_violates_contract` ingests `matches_real_sample.csv` without `started_at`);
existing 001 integration tests on `matches_real_sample.csv` (`test_idempotency`,
`test_excluded_rows`, `test_verify`), which run with the default `now()`.

**Problem**: These runs use the real clock. They are deterministic only because every fixture date
is in the past. That holds, but it is an assumption, not "no dependence". Once finding 1 is fixed,
the integration runs (real clock) and unit runs (`RUN_DATE`) also accept the same rows of
`matches_real_sample.csv`. That consistency is what keeps `test_upgrade_then_rerun_equals_fresh_build[matches_real_sample.csv]`
meaningful.

**Required change**: Reword R5 to say "no assertion depends on the real clock; fixture dates are
all in the past (max 2022-02-07)". The guard added in finding 1 covers the rest.

### 3. MINOR — The `LIKE matches` probe table keeps `match_id` NOT NULL without its identity

**Location**: T024 (`CREATE TEMP TABLE probe (LIKE matches)`), research.md R13.

**Problem**: `LIKE` without `INCLUDING IDENTITY` copies `match_id bigint NOT NULL` with no
generator. `home_team_id`/`away_team_id` are also NOT NULL. An insert of staged rows that omits
`match_id` fails before any expression is evaluated, and the implementer has to discover this.

**Required change**: Specify `CREATE TEMP TABLE probe (LIKE matches INCLUDING IDENTITY)`, or say
that the probe rows supply a synthetic `match_id` (for example the `source_row_number`) and
literal team ids. Nothing else changes.

---

## Counts

BLOCKER 0 · MAJOR 1 · MINOR 2
