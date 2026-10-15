# Plan Review — 002-data-contract, round 3

**Reviewed commit**: 4d36856 (branch `002-data-contract`)
**Scope**: the fixes for round-2 findings P2-1 … P2-3 (decisions.md), and a check that they did not
introduce new inconsistencies. I read the diff e5891d9..4d36856 (data-model.md, decisions.md,
plan.md, research.md, tasks.md). I then grepped every artifact for the old and new test dates and
scanned the `MatchDate` column of every file in `tests/fixtures/`.

Verdict: APPROVE

No BLOCKER or MAJOR findings remain. There are two MINOR implementation-detail notes.

---

## Rulings on round-2 responses

| ID | Response | Ruling | Verification |
|---|---|---|---|
| P2-1 | FIXED | Resolved | Details below. |
| P2-2 | FIXED | Resolved | Plan "Determinism rules" and R5 now say that only *contract outcomes* in tests that pass `RUN_DATE`/`CONTRACT_STARTED_AT` are clock-free. Ingests that omit `started_at` still use `now()` and are deterministic only because every fixture date is in the past, which the P2-1 guard pins. The wording is accurate. |
| P2-3 | FIXED | Resolved | T024, R13 and data-model §4 now say `LIKE matches INCLUDING DEFAULTS INCLUDING IDENTITY`. That gives `match_id` its generator. CHECK constraints are still not copied, because that needs `INCLUDING CONSTRAINTS`, and neither are FKs. The remaining team-id detail is finding 2. |

**P2-1 details:**
- `RUN_DATE` and `CONTRACT_STARTED_AT` are now 2023-06-30 (T010, plan.md, R5).
- The latest accepted fixture date in the repository is 2022-02-07 (`matches_real_sample.csv`
  row 15). Every other fixture is at or before 2021-05-09; `2021-13-01` in edge cases is an
  invalid date. So the feature-001 expectation in `test_report_counts_per_reason` (14 accepted,
  2 excluded) holds again, and R17's statement ("real sample, 14 accepted, passes") is true again.
- The new guard `test_run_date_after_all_fixture_dates` pins the invariant, and T010 explicitly
  forbids lowering feature-001 expectations.

## Consistency checks requested

- **`matches_contract.csv` expectations.** Every row is dated on or before 2020-11-04, except the
  synthetic `2999-01-01` row (row 12) and `1999-12-31` (row 11). The stated validity window
  (2020-11-04 … 2998-12-31) contains 2023-06-30. So the v1 and pre-contract outcome columns,
  the derived counts (5 loaded / 18 excluded, `contract_violation` 15, per-rule sum 16) and the
  convergence numbers (0/1/4, 13 removed, 1 exclusion removed) are unchanged. T005 now says
  "tests use 2023-06-30".
- **Convergence seed dates.** `seed_pre_contract(url, source, RUN_DATE)` evaluates
  `PRE_CONTRACT`, which has no date rule, so the seed does not depend on the date. The upgraded
  and fresh databases are both loaded with `started_at=CONTRACT_STARTED_AT`, so they share the
  same run date. With the earlier 2021 date, the 2022 real-sample row would have been a violation
  on both sides. It now loads on both sides, so `test_upgrade_then_rerun_equals_fresh_build[matches_real_sample.csv]`
  is again the plain no-op equality it was designed to be.
- **Clock check (R8).** `RunMeta.run_date = 2023-06-30` is earlier than any real database date,
  so `ClockSkewError` cannot fire in the suite except in
  `test_db_clock_behind_run_date_fails_before_writes`, which injects 2999-01-01 on purpose.
- **Remaining date literals.**
  - `2021-06-30`/`2021-07-01` appear in the artifacts only in T015
    `test_date_in_range_boundary_uses_run_start_date`, which passes its own explicit `started_at`.
    They also appear in decisions.md P1-2 and P2-1, as history.
  - `2026-10-15`/`2026-10-14` remain only in T011 `test_date_in_range_uses_injected_run_date`,
    which passes `run_date` explicitly to pure code and never touches a database or clock, and in
    document headers and real-data profile dates (R15, R16). All correct.
  - The default test date is used consistently everywhere else.

---

## Findings

### 1. MINOR — The guard test's file ownership and its input handling are loosely specified

**Location**: T010 (adds `tests/unit/test_data_contract.py::test_run_date_after_all_fixture_dates`)
and T011 (which "writes" the same file).

**Problem**:
- Two tasks own one file. T010 is sequential and T011 is marked [P], which is harmless but
  ambiguous about who creates the file.
- "Every parseable `MatchDate` in every `tests/fixtures/*.csv`" includes
  `matches_invalid_utf8.csv` and the ragged and duplicate-header files. A strict
  `extract.read_source` would raise on those files rather than skip them.

**Suggested change**: Either move the guard into T011's list, or say that T010 creates the file.
Specify that the guard scans raw bytes or lines with a `^[^,]*,(\d{4}-\d{2}-\d{2})` regex and a
`date.fromisoformat` try/skip, rather than using the pipeline reader.

### 2. MINOR — Team ids for probe rows in the fixture differential test are unspecified

**Location**: T024 `test_db_constraints_agree_with_pipeline_on_fixture_rows`, R13.

**Problem**: The probe table keeps `home_team_id`/`away_team_id NOT NULL`. The staged rows carry
team keys, not ids, and the fixtures involve many teams. The per-case test seeds ids 1 and 2, but
the fixture variant does not say where ids come from.

**Suggested change**: State that probe rows use literal ids (for example 1 and 2). No contract
expression reads team ids, and `LIKE` copies no FK or `home <> away` CHECK. The alternative is to
upsert the batch's teams first.

---

## Counts

BLOCKER 0 · MAJOR 0 · MINOR 2
