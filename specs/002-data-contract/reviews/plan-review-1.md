# Plan Review — 002-data-contract, round 1

**Reviewed commit**: f313766 (branch `002-data-contract`)
**Artifacts**: constitution 1.1.0, spec.md, plan.md, research.md, data-model.md, contracts/*,
quickstart.md, tasks.md. `decisions.md` does not exist yet, so there are no rebuttals to rule on.
**Checked against the real code**: `src/football_stats/{load,validate,clean,pipeline,source_contract}.py`,
`migrations/versions/0001_initial_schema.py`, `tests/stages.py`, `tests/integration/{conftest,helpers,test_migrations,test_constraints,test_excluded_rows}.py`,
`scripts/check.sh`, `scripts/smoke_compose.sh`. I also evaluated the nine proposed rules (R3 semantics)
on the accepted rows of the three existing fixtures with the project's own parsers. Result:
`matches_valid.csv` 0 violations, `matches_real_sample.csv` 0, `matches_edge_cases.csv` 1
(`ht_le_ft_home`, row 19). That matches R17.

Verdict: REVISE

One MAJOR finding (a constitution conflict that takes one line to fix) and nine MINOR ones. The core
design holds up under adversarial checking. Below are the parts I tried to break and could not:

- **Convergence (FR-009, R10)**: I traced every row-lifecycle transition in data-model §6
  against the real upsert SQL. `_NON_KEY_COLUMNS` includes `source_sha256` and
  `source_row_number`, so the `IS DISTINCT FROM` upsert re-stamps provenance. After step 5, every
  staged key therefore carries a staged provenance, and the anti-join in step 6 deletes exactly
  "provenance S, no longer produced by S". Normalisation-induced key moves and merges also
  converge: the old key is deleted and the new key is inserted or kept. The duplicate swap
  (row 17 → 18) updates in place and survives the delete. Keys shared with another file are
  overwritten (001 last-writer-wins) or left alone, which matches the spec's scope. Orphan
  deletion is global but can only remove rows that nothing references. Under 001, no orphans can
  exist other than the ones convergence creates.
- **Fixture arithmetic**: I recomputed the v1 and pre-contract outcomes of all 23
  `matches_contract.csv` rows by hand. The expected values are correct: loaded {1,14,15,16,18};
  `contract_violation` 15; per-rule sum 16; pre-contract loaded 18; re-run 0/1/4 with 13 removed
  and 1 exclusion removed. The quickstart §5 detail breakdown also sums correctly
  (1356+10+7+4+3+1 = 1381; per-rule 7/13/8/1357).
- **Content snapshot (R12)**: it compares every column of `matches` (via `to_jsonb`, including
  provenance), `(team_key, name)`, leagues, and excluded rows including `raw_record`. Only
  surrogate ids and `run_id` are left out. It would catch a stale match, a stale or missing
  exclusion, a wrong team name, an orphan, or wrong provenance. It is a real comparison, not a
  count check.
- **NOT VALID + statement order (R9)**: the argument is correct. The `DO UPDATE` writes the full
  staged tuple (all non-key columns, and the keys are equal by conflict), so the new tuple is a
  pipeline-accepted tuple. A skipped update writes nothing, and DELETEs never evaluate CHECKs.
  Upsert-then-delete is safe and keeps `match_id` stable. Delete-then-upsert would also be safe;
  R9 correctly prefers the former.
- **NULL and float parity (R3)**: terms are skipped per input on both sides. The SQL CASE has no
  `ELSE`, so NULL goals give NULL, which counts as a pass. `float8 >= 1.0` resolves to a
  float8 comparison, which is exact. `write_csv` emits shortest round-trip text and PostgreSQL
  parses it exactly. NaN and ±inf cannot reach either side because `clean._decimal` requires
  `is_finite()`. Negative odds parse and are caught by `odds_min`.
- **`now()` in a CHECK (R8)**: the constraint is `NOT VALID`, so pg_dump emits it as a separate
  `ADD CONSTRAINT … NOT VALID` after the data and no restored row is checked. Even if it is
  validated, the bound only moves forward, so a row that was valid stays valid on restore.
  `AT TIME ZONE 'UTC'` removes the dependence on the session time zone. The remaining risk is
  clock origin; see finding 2.
- **Gate**: `scripts/check.sh` already covers format, lint, mypy, unit (sockets disabled, fixed
  seed) and integration. The plan adds no network, no wall-clock assertions and no shared state.
  It injects the run date, and the only `now()` assertion reads the database date inside the same
  transaction.
- **Coverage**: every FR-001…FR-012 and SC-001…SC-006 maps to tasks and named tests. Only FR-011,
  SC-005, SC-006 and the numbers part of FR-012 are manual (T036). Those require network access
  and a stopwatch, so manual is acceptable. Everything automatable around them is in the gate: the
  rule list in the README, per-rule counting, and the upgrade path through `seed_pre_contract`.
  Findings 6, 8 and 10 cover the small automatable remainder.

---

## Findings

### 1. MAJOR — Recommending a persistent, hand-run `VALIDATE CONSTRAINT` breaks "migrations only" and contradicts R8

**Location**: research.md R8, Decision paragraph ("The README documents the optional manual
`ALTER TABLE matches VALIDATE CONSTRAINT matches_contract_<id>` once all sources are converged").
T034 builds the README from these decisions.

**Problem**: The constitution (Technical Constraints) says schema changes "MUST be made through
versioned migrations … never by hand". A committed `VALIDATE CONSTRAINT` changes the catalog
persistently (`convalidated` flips, and `pg_get_constraintdef` loses `NOT VALID`). That is a
by-hand schema change. It also contradicts R8's own reason for rejecting a conditional
`VALIDATE` ("catalog then depends on data history"). A database validated by hand no longer
matches what `alembic upgrade head` produces, and nothing in the project would know. The
quickstart §2 use, inside `BEGIN … ROLLBACK`, is fine because it is a read-only proof.

**Required change**: Remove the "optional manual VALIDATE" advice from R8 and make sure T034
does not add it to the README. If validating later is wanted, state that it ships as a future
Alembic revision. Keep only the rolled-back `VALIDATE` used as a whole-table SC-001 check, and
label it as such in the README and quickstart.

### 2. MINOR — `date_in_range` parity assumes one clock; the "always ≥" claim is not guaranteed

**Location**: research.md R5/R8 ("the database bound is always ≥ the pipeline bound"), plan.md
Determinism rules, T015 `test_date_in_range_boundary_uses_run_start_date`, T021
(`pipeline.run(..., started_at=...)`).

**Problem**: The pipeline's `run_date` comes from the Python host clock, or from any caller of
the public `pipeline.run(started_at=...)`. The database bound comes from the PostgreSQL server
clock. These are different clocks: testcontainers on Docker Desktop runs in a VM whose clock is
known to drift after the laptop sleeps, and a managed database is another host. A future
`started_at`, or a DB clock behind the app clock, makes the pipeline accept a row dated "today"
that the database rejects. FR-007 says this must never happen: the result would be an opaque
`LoadError` and a failed run. T015 also deliberately loads a row dated `2026-10-15` into a real
database, so it passes only if the database clock's UTC date is at least 2026-10-15.

**Required change**: (a) Correct the claim in R8 to "guaranteed when app and DB share a clock".
(b) In `load_run`, after taking the lock, compare `(now() AT TIME ZONE 'UTC')::date` with
`run_date`. If the DB date is earlier, fail with a named error *before* any write instead of an
anonymous CheckViolation. Pass `run_date` through `RunMeta`. (c) Move the T015 boundary test to
a past date, for example `started_at = 2021-06-30 23:00 UTC` with rows dated 2021-06-30 and
2021-07-01, so it does not depend on the real clock.

### 3. MINOR — Parity test cannot see the full violated set for multi-rule cases; a few NULL combinations are missing

**Location**: T024 `test_db_agrees_with_pipeline_on_contract_case`; plan.md `contract_cases.py`
table.

**Problem**: PostgreSQL reports only the first failing CHECK, by name order. For `multi_rule` and
`max_odd_below_one`, the test only checks membership. It cannot detect a DB constraint that is
stricter or looser than its pipeline twin on a row that another constraint already rejects. The
table also lacks some skip combinations that the semantics depend on: `ht_result` NULL with both
HT goals present; an average odd NULL with its maximum present (only the reverse is covered);
and a draw- or away-only odds case.

**Required change**: In the integration parity test, evaluate each constraint separately. Insert
the case into a constraint-free temp table with `LIKE matches`, then for each
`pg_get_constraintdef` expression run `SELECT <expr> IS NOT FALSE`. Assert that the set of failing
`matches_contract_*` names equals `{matches_contract_<r> for r in case.violated}`. Add the three
cases above. Optionally, run the same per-constraint comparison over every row of
`matches_contract.csv` and `matches_real_sample.csv` parsed with `PRE_CONTRACT`, as a cheap
differential test.

### 4. MINOR — Pandera "cannot be disabled" and completeness are only partly pinned

**Location**: research.md R2 facts 3 and 5; T002; T011.

**Problem**: The env-var route (`PANDERA_VALIDATION_ENABLED=False` or
`PANDERA_VALIDATION_DEPTH=SCHEMA_ONLY` set *before import*) was verified only in the probe. T011
cannot test it in-process because pandera is already imported. No test asserts that
`failure_cases` is complete for a large number of failures. A cap or truncation in a future
0.34.x patch would let violators through, and the run would then fail at the DB. The R2
behaviour "a check that raised → `ContractSchemaError`" is also untested.

**Required change**: Add to `test_pandera_backend.py` or `test_data_contract.py`:
(a) a `subprocess` test (no network) that sets both env vars and asserts `find_violations` still
reports a known violation; (b) a completeness assertion, for example 50,000 failing rows giving
50,000 failure cases and 50,000 violation rows; (c) a test with a rule whose condition raises,
asserting `ContractSchemaError`.

### 5. MINOR — T029 needs a third database that no fixture provides

**Location**: T029 `test_rerun_loads_rows_whose_exclusion_no_longer_applies`, part 2 ("a third
fresh DB loaded once with `CURRENT_CONTRACT`"); conftest has exactly one `db_url` and one
`empty_db_url` per test.

**Required change**: Add a factory fixture in T007, for example `make_db_url()` that clones
`template_db` and drops it on teardown. Alternatively, split part 2 into its own test using
`db_url` and `empty_db_url` + `upgrade`.

### 6. MINOR — The FR-010 convergence rollback test passes before the code exists and has no positive control

**Location**: T030 `test_failure_after_convergence_rolls_back`.

**Problem**: Before T031, `load_run` also fails at the excluded-row upsert and nothing is deleted,
so the test passes immediately. The task does not list it as an exception. After T031, the test
cannot tell "deletes ran and were rolled back" from "deletes never ran before the failure".

**Required change**: Mark it as a regression guard. Add a positive control in the same test: run
the same `load_run` with the valid excluded frame afterwards and assert `matches_removed == 13`.
Alternatively, assert ordering through the `converge …` log line captured before the failure.

### 7. MINOR — R18's downgrade-refusal claim is untested

**Location**: research.md R18 and data-model §1.4 ("fails, and is rolled back, while
`contract_violation` rows exist"); the round-trip test only runs on an empty database.

**Required change**: Add `test_downgrade_refused_with_contract_violation_rows`: ingest
`matches_contract.csv`, call `downgrade("0001_initial_schema")`, expect an error, and assert that
`alembic_version` and `catalog_snapshot` are unchanged. Alternatively, drop the claim.

### 8. MINOR — Logged counts are specified but not tested

**Location**: contracts/cli.md Logs (`contract rule=<id> violations=<n>`, `converge …`); constitution
Code Quality "every pipeline run MUST log its row counts".

**Required change**: In `test_run_report_and_record_contract_metadata` and
`test_rerun_removes_now_violating_matches`, use `caplog` to assert the nine per-rule lines (zeros
included) and the `converge` line with the expected numbers.

### 9. MINOR — Performance claim about indexes is inaccurate; temp tables are not analysed

**Location**: plan.md Constitution Check ("convergence deletes use existing indexes"); R15.

**Problem**: The stale-match DELETE filters `matches` by `source_sha256`, which has no index, and
anti-joins against temp tables that are never `ANALYZE`d. It is a full scan of about 238k rows,
which is acceptable, but the claim is wrong and the plan quality depends on estimates. SC-005 is
only checked manually.

**Required change**: Correct the wording. In T031, run `ANALYZE stg_matches, stg_excluded` after
the COPY so the anti-joins get hash plans. T036 already records the timing.

### 10. MINOR — Small test-spec gaps and ambiguities

- T012 `test_invalid_later_copy_is_contract_violation_not_duplicate` says "on `FULL_ROW`", but
  `partition_rows` has its own base row with no shots. Spell the rows out as
  `[{**FULL_ROW}, {**FULL_ROW, "AwayTarget": "10.0"}]`. With the default base, `sot_le_shots_away`
  is skipped and the row becomes a `duplicate`.
- contracts/cli.md lists `contract_violation <= sum(violations_by_rule)` and "both 0 together" as
  "Invariants (tested)", but no task asserts them generically. Add them to T013 or T016
  (`test_cli_ingest_report_matches_schema`).
- T034 writes placeholders into "Measured numbers — data contract", and only T036 (manual) fills
  them. Add to T037 an explicit check that the section has no placeholder token left, so
  FR-012/SC-006 cannot be closed with placeholders.

---

## Counts

BLOCKER 0 · MAJOR 1 · MINOR 9 (finding 10 bundles 3 small items)
