# Plan Review — 003-dbt-transforms, round 2

**Reviewed commit**: 07673d1 (branch `003-dbt-transforms`)

**Scope**: the planner's responses to round-1 findings P1-1 … P1-10 (decisions.md). I read the
full diff e73b40a..07673d1 of spec.md, plan.md, research.md (including the new R18),
data-model.md, contracts/transform.md, quickstart.md and tasks.md. I then grepped every artifact
for leftovers of the old wording: "7 dbt invocations", "six rows", `Index Scan` assertions, "models
not yet built … skipped", "computed by hand", `unique_mart_team_form_points` and "No constitution
violations". None remain outside decisions.md. I also re-checked the new mechanisms against the
code: `load.py` inserts teams `ORDER BY team_key` and matches `ORDER BY source_row_number`, and
there is the existing `template_db` clone pattern.

Verdict: APPROVE

No BLOCKER or MAJOR findings remain. There is one MINOR note, which is optional and does not
block.

---

## Rulings on round-1 responses

There are no rebuttals. Every response is FIXED, and I verified each fix in the artifacts.

| ID | Response | Ruling | Verification |
|---|---|---|---|
| P1-1 (MAJOR) | FIXED | Resolved | See "P1-1 details" below. |
| P1-2 (MAJOR) | FIXED | Resolved | See "P1-2 details" below. |
| P1-3 | FIXED | Resolved | See "P1-3 details" below. |
| P1-4 | FIXED | Resolved | See "P1-4 details" below. |
| P1-5 | FIXED | Resolved | See "P1-5 details" below. |
| P1-6 | FIXED | Resolved | See "P1-6 details" below. |
| P1-7 | FIXED | Resolved | See "P1-7 details" below. |
| P1-8 | FIXED | Resolved | See "P1-8 details" below. |
| P1-9 | FIXED | Resolved | See "P1-9 details" below. |
| P1-10 | FIXED | Resolved | See "P1-10 details" below. |

**P1-1 details**

- The Constitution Check row "II. Safe to retry" now cites the test that proves the property. In
  `test_build_fails_and_names_failing_test` (T010), the failing build exits 1, the failing test is
  named, and the downstream models are asserted `skipped`. Then the unmodified project is rerun on
  the **same** database: exit 0, `analytics_snapshot` equal to the session build's, and `public`
  (including `alembic_version`) equal to the state before the failing build.
- The ids are comparable across the two databases. Both are template clones with the same ingest,
  teams get ids in `team_key` order and matches in `source_row_number` order, so the snapshot
  equality is sound.
- R18 states the failed-build state accurately:
  - a mart whose own test failed stays published;
  - skipped marts keep their previous contents, or are absent on a first build;
  - a model's own SQL or contract failure rolls back its swap.
- The same statement is in contracts/transform.md "State after a non-zero exit" and is carried
  into README task T035.
- Rejecting blue/green schema swaps is reasonable. It would need hooks or run-operations, which R4
  forbids. Phase 4 can gate consumers on the exit code.
- The invocation count is now 8 (R5 and plan.md Performance Goals).

**P1-2 details**

- Complexity Tracking now opens with two labelled items:
  - Deviation 1, the interpretation that mart indexes and keys live in dbt config;
  - Deviation 2, no database FKs on marts.
- The FK rationale is now correct. An FK to `public` would make 002's convergence `DELETE` fail or
  cascade into analytics. FKs between marts cannot survive the tmp-table swap. `relationships`
  tests are the substitute.
- The Constitution Check result line and the "Tech" row agree with Complexity Tracking.
- spec.md Assumptions has the ruling R-A line, and it is a clarification only.

**P1-3 details**

- data-model.md §4 has been extended:
  - `assert_team_form_two_rows_per_match` now compares per-side `goals_for`, `goals_against`,
    `result`, `points`, `league_code`, `season` and `match_date` with `stg_matches`.
  - The home-advantage reconcile test now recomputes all three shares.
  - The goals reconcile test now recomputes `goals_per_match`, `over_2_5_share` and `btts_share`.
- Three mutation cases were added: `team_form_goals_swapped`, `home_away_shares_swapped` and
  `btts_share_off`. Against the fixture each one changes at least one row:
  - rows with `goals_for <> goals_against` exist;
  - the ARG home-win and away-win shares are 1.0000 and 0.0000;
  - +0.0001 on every `btts_share` stays within `numeric(5,4)`.
- `test_singular_tests_cover_every_file` uses set equality on test names, which means at least one
  case per file.

**P1-4 details**

- T024 now asserts structure only:
  - exit 0;
  - the six labels `Q1` … `Q6` are present;
  - there are 5 × 11 `Execution Time:` lines;
  - both catalog snapshots are unchanged.
- The variant counts in data-model.md §6 (2+2+2+1+2+2 = 11) agree with R14: Q4 is the existing
  natural-key plan only.
- T026 forbids `SET enable_*`.

**P1-5 details**

- The literal `EXPECTED_MODELS` and the key and FK dicts are in T010, and nothing is skipped.
- T018 extends them to all 7 models before the mart SQL is written, so the tests fail first.
- T010's expected failure is now correctly stated: an empty build exits 0, so the failure is
  missing model names or relations.

**P1-6 details**

- The YAML round trip (`safe_load` → append `"unique"` to `home_points.data_tests` → `safe_dump`)
  is specified. `home_points` already has an `accepted_values` entry, so the list exists.
- quickstart §3 uses the same file, column and test name as T010, warns about the exit-2 parse
  error, and expects the four marts `SKIP` followed by a recovering rerun.

**P1-7 details**

- `cwd` is the parent of `DBT_TARGET_PATH` (data-model.md §5, T007).
- `DBT_SEND_ANONYMOUS_USAGE_STATS=false` is set in `dbt_env` (asserted in T006) and in the image ENV
  (T029).
- The deprecation check is in `test_build_succeeds_and_runs_every_node`.
- The wrapper is `SELECT count(*) FROM (\n{sql}\n) t`.
- T002 rejects `;` in singular tests.

**P1-8 details**

- `helpers.public_state` is the triple of snapshot, catalog and `alembic_version` rows. It is used
  by the session fixture, the unchanged-public test, the idempotency test and the recovery test.
- quickstart §1 hashes all 7 models, each ordered by its key, and T036 records that all 7 match.

**P1-9 details**

- `analytics_snapshot(conn, models=tuple(ANALYTICS_MODELS))` has the same signature in
  data-model.md §5 and T008.
- T005 states `H` → `true`, `A` → `false`.
- T005 updates the provenance text for SOURCES.txt.

**P1-10 details**

- The pin test now uses `packaging` `SpecifierSet` semantics: `1.12.0` is excluded and `1.11.99`
  is admitted. `packaging` is already installed through pytest and dbt, and it is typed.
- The Compose test asserts properties. It reads the variable names from `profiles.yml` instead of
  restating them.
- The Dockerfile structural checks are kept, and they carry R2's behaviour.

---

## Findings

### 1. MINOR (optional) — The recovery test starts from a first build, so it never exercises the "mixed" state

**Location**: tasks.md T010 (`test_build_fails_and_names_failing_test`); research.md R18.

**Problem**: the failing build runs on a freshly ingested database, and the injected failure sits on
`stg_matches`. The four marts are therefore skipped and never created. The rerun shows recovery from
"marts absent", not from the state R18 warns about: marts from two runs, or a failed mart still
published.

The convergence argument still holds by construction: a full table rebuild ignores prior contents.
`test_second_build_is_identical` already covers rebuilding over existing tables, including the PK
names that alternate between builds. So this is not a gap in the proof, only in how directly the
test illustrates R18.

**Suggested change (optional, about 3 s)**:
1. Run one successful build on that database first.
2. Inject the failing test on a mart column, for example `mart_team_form.points`, so that staging
   and the other marts are republished while team-form fails.
3. Then run the recovery build.

If adopted, update the invocation count in R5 to 9.

### Note (no action)

The row counts that R10 records for the probe cover only the original six mutation cases. The three
new cases have not been probed. T016 will establish them. Their effect on the fixture is clear from
the data, as checked above.

---

## Counts

BLOCKER 0 · MAJOR 0 · MINOR 1
