# Decisions: Plan Review Responses

**Feature**: `002-data-contract`

## Round 1 (reviews/plan-review-1.md)

All 10 findings are FIXED; none is rebutted. Each pointed to a real gap. On two of them the fix
differs from what the reviewer suggested, with the reason given:
- **P1-5**: I split the test instead of adding a database factory fixture.
- **P1-7**: the expected outcome in the requested test was wrong for this repository's Alembic
  configuration. The test now asserts the actual, correct behaviour.

No task was added or removed, so task IDs T001–T037 are unchanged. New tests were added inside
existing tasks.

Before answering, I ran two new probes:
- **pandera 0.34.1** (scratch project, no repository change): a check whose expression raises
  becomes a failure case with a null `index`, not an exception. 50,000 failing rows give 50,000
  failure cases.
- **`migrations/env.py`**: it sets `transaction_per_migration=True`.

I also re-verified all 27 shared parity cases, including the three new ones, against the R3
expressions on the project's own parsers. There were 0 mismatches.

### P1-1: Recommending a persistent, hand-run `VALIDATE CONSTRAINT` breaks "migrations only" and contradicts R8
Finding: R8 told the README to document a manual `ALTER TABLE … VALIDATE CONSTRAINT`. Committed,
this is a schema change outside Alembic, and it makes the catalog depend on history.

Response: FIXED

Reasoning: The reviewer is right. It also contradicts R8's own reason for rejecting a conditional
`VALIDATE` in the migration.
- R8 now says constraints are never validated by hand. Persistent validation, if ever wanted,
  ships as a new Alembic revision.
- The only `VALIDATE` in the feature is a read-only proof inside `BEGIN … ROLLBACK`. The quickstart
  §2 text labels it that way and says never to run it outside a rolled-back transaction.
- The two automated SC-001 uses (T026 `test_no_loaded_match_violates_contract`, T029
  `test_upgrade_then_rerun_equals_fresh_build`) now also run in a rolled-back transaction. T026
  asserts afterwards that every `matches_contract_*` is still `convalidated = false`.
- T034 forbids the advice in the README. T037 greps the README for it.

### P1-2: `date_in_range` parity assumes one clock; the "always ≥" claim is not guaranteed
Finding: The pipeline's run date comes from the app host (or any `started_at`), while the DB bound
comes from the PostgreSQL server clock. Skew turns into an anonymous mid-load failure. T015 also
depended on the real DB clock.

Response: FIXED (all three parts)

Reasoning:
- **(a) Claim corrected.** R8 now states that the guarantee holds only when the app and DB share a
  clock, and names the cases where they do not: Docker VM drift, a remote DB, an explicit
  `started_at`.
- **(b) Up-front check.** `RunMeta` gains `run_date`. Right after the advisory lock and before
  any write, `load_run` compares `(now() AT TIME ZONE 'UTC')::date` with it. If the database date
  is earlier, it raises `ClockSkewError`, a new exit 10 `clock_skew` naming both dates.
  - I chose a dedicated code over reusing exit 7 because the remedy (fix a clock) differs from a
    database failure.
  - The change touches errors.py (T017), load.py (T021), contracts/cli.md, the report schema enum,
    data-model §5 step 1a, research R8 and R14, and the plan's Complexity Tracking.
  - New tests: `test_db_clock_behind_run_date_fails_before_writes` (run date 2999-01-01, snapshot
    unchanged) and `test_clock_skew_error_exit_code`.
- **(c) No real-clock dependence.** The boundary test now uses 2021-06-30/2021-07-01 with
  `started_at` 2021-07-01T01:00+02:00 (= 2021-06-30 23:00 UTC), so the UTC conversion stays
  covered.
  - `RUN_DATE` and `CONTRACT_STARTED_AT` moved to 2021-06-30. The `matches_contract.csv`
    expectations are valid for any run date from 2020-11-04 to 2998-12-31, so no expected value
    changed.
  - The only remaining `now()`-dependent tests read the database's own date inside the same
    transaction.

### P1-3: Parity test cannot see the full violated set for multi-rule cases; a few NULL combinations are missing
Finding: PostgreSQL reports only the first failing CHECK, so for multi-rule cases a constraint that
is stricter or looser than its pipeline twin would go unnoticed. Three skip combinations were
missing.

Response: FIXED

Reasoning: The reviewer's method is cheap and makes parity a set equality per row.
- T024 now copies the parsed row into `CREATE TEMP TABLE probe (LIKE matches)`. `LIKE` without
  `INCLUDING CONSTRAINTS` copies no CHECKs.
- It evaluates every `matches_contract_*` expression, read with `pg_get_expr(conbin, conrelid)`,
  as `(<expr>) IS NOT FALSE`. It then asserts that the failing set equals the expected rule set.
  The real INSERT is kept as well.
- Three cases were added:
  - `ht_result_missing_with_goals`: `HTResult` NULL, HT goals present → no violation.
  - `avg_missing_max_present`: `OddHome` NULL, `MaxHome` present → no violation.
  - `draw_only_max_below_avg`: only draw odds present, max < avg → `max_odds_ge_avg`.
- I also added the optional differential test `test_db_constraints_agree_with_pipeline_on_fixture_rows`
  over every accepted row of three fixtures. It costs a few lines and covers combinations nobody
  thought to write down.
- Updated: research R13, data-model §4, plan.md case table (now 27 cases) and mapping, T006, T024.

### P1-4: Pandera "cannot be disabled" and completeness are only partly pinned
Finding: The env-var route was verified only in a probe. No test checked completeness with many
failures. "A check that raised → `ContractSchemaError`" was untested.

Response: FIXED

Reasoning: All three are claims the design relies on, so they belong in the gate. T011 gains:
- `test_find_violations_ignores_pandera_env_disable`: a subprocess, with no network, that sets
  `PANDERA_VALIDATION_ENABLED=False` and `PANDERA_VALIDATION_DEPTH=SCHEMA_ONLY` before import and
  still gets `ft_result_consistent`.
- `test_find_violations_is_complete_for_many_failures`: 50,000 violating rows give 50,000
  violation rows.
- `test_find_violations_raising_rule_raises_contract_schema_error`: a rule reading a column
  outside its inputs.

The new probe confirmed the mechanism behind the last one: pandera reports a raising check as a
failure case with a null `index`, which R2 already maps to `ContractSchemaError`. It is now
recorded as R2 fact 7, and completeness as fact 8. These tests exercise our `find_violations`, not
only the library, so an upstream change surfaces as a failing test of our guarantee.

### P1-5: T029 needs a third database that no fixture provides
Finding: Part 2 of `test_rerun_loads_rows_whose_exclusion_no_longer_applies` compared against "a
third fresh DB", but each test gets only `db_url` and `empty_db_url`.

Response: FIXED (by splitting the test, the reviewer's second option)

Reasoning: Part 2 moved into its own test, `test_reload_after_loosened_contract_equals_fresh_build`:
- It loads with a stricter test contract, then with `CURRENT_CONTRACT`, on `db_url`.
- The fresh comparison database is `empty_db_url` + `helpers.upgrade`.

Two databases per test is all it needs. That is less machinery than a database factory fixture
used by a single test (Principle V). The test is added to the FR-009 mapping row.

### P1-6: The FR-010 convergence rollback test passes before the code exists and has no positive control
Finding: Before T031 nothing is deleted, so the test is green before the code exists. After T031 it
cannot tell "deletes ran and were rolled back" from "deletes never ran".

Response: FIXED (positive control, as the orchestrator also asked)

Reasoning: After the failing call, the same test calls `load_run` again on the same connection
with the unmodified excluded frame.
- It asserts `matches_removed == 13`, `excluded_rows_removed == 1`, and that the 13 matches are
  gone.
- Before T031 the counters are 0, so the test is red. After T031 it is green: real red-then-green.
- Together the two halves show that the same `load_run` deletes those rows on success, and that on
  failure the database equals the pre-run snapshot. The failure is injected at the excluded-row
  upsert, which by the data-model §5 order runs after the deletes. So the first half observes
  deletes that ran and were rolled back.

### P1-7: R18's downgrade-refusal claim is untested
Finding: The claim that the downgrade "fails and is rolled back" while `contract_violation` rows
exist had no test.

Response: FIXED, and the claim itself was partly wrong

Reasoning: While writing the test I checked `migrations/env.py`. It uses
`transaction_per_migration=True`, so a downgrade from head to `0001` behaves like this:
1. It **commits** `0003`'s downgrade (constraints dropped).
2. Only `0002`'s downgrade is refused and rolled back.

The reviewer's suggested assertion ("`alembic_version` and `catalog_snapshot` unchanged") would
therefore have failed against correct code. The database ends at `0002_contract_run_metadata`, and
that state is consistent: its version row matches its schema, and no exclusion record is lost,
which is the property Principle I cares about.
- R18 and data-model §1.4 now describe this precisely.
- The new `test_downgrade_refused_with_contract_violation_rows` (T025) asserts:
  - the downgrade raises;
  - `alembic_version` is `0002_contract_run_metadata`;
  - no contract constraints remain;
  - the 0002 columns and the seven-code CHECK are still present;
  - all 15 `contract_violation` rows are kept.
- I did not switch `env.py` to a single transaction. That would change feature 001's migration
  behaviour for a downgrade edge case.

### P1-8: Logged counts are specified but not tested
Finding: The `contract rule=…` and `converge …` log lines in contracts/cli.md were not asserted.

Response: FIXED

Reasoning: Both tests now use `caplog` on logger `football_stats` at INFO:
- `test_run_report_and_record_contract_metadata` (T015) asserts the nine per-rule lines with the
  fixture counts, zeros included.
- `test_rerun_removes_now_violating_matches` (T029) asserts
  `converge matches_removed=13 excluded_rows_removed=1 teams_removed=1 leagues_removed=1`.

contracts/cli.md names these tests.

### P1-9: Performance claim about indexes is inaccurate; temp tables are not analysed
Finding: The stale-match DELETE has no index on `source_sha256` (full scan), and the anti-joins run
against temp tables with no statistics.

Response: FIXED

Reasoning: The claim was wrong for the stale-match delete. Only the orphan deletes use indexes.
- R11, R15 and the plan's Constitution Check row now say so.
- T031 runs `ANALYZE stg_matches, stg_excluded` after the COPY. COPY into a fresh temp table leaves
  no statistics, so the planner would otherwise guess the anti-join sizes.
- I did not add an index on `source_sha256`. A sequential scan of about 238k rows once per run is
  small against the 5-minute budget, and T036 records the measured time. An index would cost
  write time on every upsert for a speculative gain (Principle V; the constitution asks for
  measured, not estimated, performance claims).

### P1-10: Small test-spec gaps and ambiguities
Finding: (a) T012's later-copy test would silently skip the rule with `partition_rows`' base row.
(b) The cli.md invariants were not asserted generically. (c) README placeholders could survive into
the final state.

Response: FIXED (all three)

Reasoning:
- **(a)** T012 now spells the rows out as `[{**FULL_ROW}, {**FULL_ROW, "AwayTarget": "10.0"}]` and
  explains why. With the default base row, the shots columns are empty, the rule is skipped, and
  the row becomes a `duplicate`. That is exactly the mistake the reviewer predicted.
- **(b)** New `test_report_invariants_hold[*]` over five fixtures in T013. T016 also checks the
  invariants on the CLI report in `test_cli_ingest_report_matches_schema`.
- **(c)** T034 must use the literal placeholder `TBD-T036`, and T037 checks that
  `grep -n "TBD-T036" README.md` prints nothing.

### P2-1: Default RUN_DATE 2021-06-30 turns a feature-001 real-sample row into a contract violation
Finding: `matches_real_sample.csv` row 15 is dated 2022-02-07; under `RUN_DATE = 2021-06-30` it
fails `date_in_range`, breaking `test_accounting.py::test_report_counts_per_reason` (14/2 → 13/3)
and falsifying P1-2(c) and R17.

Response: FIXED

Reasoning: The reviewer is right; P1-2(c)'s "no expected value changed" only checked
`matches_contract.csv`. `RUN_DATE` and `CONTRACT_STARTED_AT` move to 2023-06-30, after the latest
fixture date (2022-02-07) and still in the past. T010 adds
`test_run_date_after_all_fixture_dates` so a new fixture can't silently regress this. The
`date_in_range` boundary test keeps its own explicit 2021-06-30/2021-07-01 dates and
`started_at`, which is independent of the default. Feature-001 expectations are unchanged.

### P2-2: "No test depends on the real clock" overstated
Finding: Ingests without `started_at` (including feature-001 tests) still use `now()`.

Response: FIXED

Reasoning: plan.md and research R5 now say contract outcomes don't depend on the real clock for
tests that pass `RUN_DATE`/`CONTRACT_STARTED_AT`, and that the remaining `now()` ingests are
deterministic only because every fixture date is in the past (which the P2-1 guard test also
pins).

### P2-3: `LIKE matches` probe table lacks the identity generator
Finding: `match_id` stays NOT NULL without a generator, so inserts omitting it fail before any
check.

Response: FIXED

Reasoning: T024, R-parity text and data-model now use
`LIKE matches INCLUDING DEFAULTS INCLUDING IDENTITY`; CHECK constraints are still not copied
(that needs `INCLUDING CONSTRAINTS`).

## Implementation deviations

### I-1: `test_run_date_after_all_fixture_dates` lives in T011's file and scans raw bytes
Deviation: tasks.md T010 adds the guard to `tests/unit/test_data_contract.py` and T011 then writes
the same file. The guard is written once, as part of T011, and T010 only changes
`tests/stages.py`. It scans the raw bytes of every `tests/fixtures/*.csv` with the regex
`^[^,\r\n]*,(\d{4}-\d{2}-\d{2})` (second field of each line) and `date.fromisoformat` with
skip-on-`ValueError`, not the pipeline CSV reader.
Reasoning: plan-review round 3, minor finding 1. Some fixtures are intentionally invalid UTF-8,
ragged or have a duplicate header, so the strict reader would raise on them. Two tasks owning one
file was ambiguous. Behaviour is the same as specified: every parseable `MatchDate` except the
synthetic `2999-01-01` is `<= RUN_DATE` and `<= CONTRACT_STARTED_AT.date()`.

### I-3: One feature-001 test edits its input so it stays contract-valid
Deviation: `tests/integration/test_idempotency.py::test_changed_source_updates_match_in_place`
rewrites row 1 to full time 0-3 `A` and, after this change, also rewrites half time to 0-1 `A`.
The expectation `(inserted, updated, unchanged) == (0, 12, 0)` is unchanged.
Reasoning: the old edit left half time at 1-0 `H`, which breaks `ht_le_ft_home` (half-time home
goals 1 exceed full-time 0). Under contract version 1 that row is now correctly excluded and the
test saw 11 updates. This is the same situation as the repaired `matches_edge_cases.csv` rows
(research.md R17): the test is about in-place updates, not about half-time data, so its input was
made consistent. No assertion was lowered.

### I-4: `Partition.violations` and `Partition.rule_ids` have defaults
Deviation: the two new `Partition` fields have defaults (an empty violations frame, no rule ids)
instead of being required.
Reasoning: feature 001's `test_partition_rejects_inconsistent_counts` builds
`Partition(accepted=..., excluded=..., rows_read=...)` directly, and the instructions forbid
editing a feature-001 test expectation. `validate.partition` always passes both fields.

### I-5: `test_verify_cli_report_matches_schema` tampers with a different row
Deviation: the test bumps `ft_away_goals` of source row 9 (Everton 4-0 Derby) instead of row 2
(Liverpool 0-0 Everton). The assertion is the same shape: exactly one difference on
`ft_away_goals`, source `"0"`, database `"1"`; only the row number in the expected difference
changed from 2 to 9.
Reasoning: once `0003_contract_constraints` exists, bumping the away goals of a 0-0 draw makes
the stored row inconsistent with `ft_result`, so the database (correctly) refuses the UPDATE. Row 9
stays consistent after the bump (4-1, `H`, half time 1-0), so the test still creates a real
database/source difference. Nothing was weakened.

### I-2: Fixture-wide parity probe rows use literal team ids
Deviation: `test_db_constraints_agree_with_pipeline_on_fixture_rows` (and, for uniformity, the
per-case parity test and the update test) insert staged rows with literal `home_team_id = 1` and
`away_team_id = 2` instead of resolving team keys to ids.
Reasoning: plan-review round 3, minor finding 2. No contract expression reads team ids, and
`CREATE TEMP TABLE probe (LIKE matches INCLUDING DEFAULTS INCLUDING IDENTITY)` copies no foreign
key and no `home <> away` CHECK. Upserting every fixture team first would add code that the test
does not need. The per-case test still seeds teams 1 and 2 because it also does a real INSERT
into `matches`.
