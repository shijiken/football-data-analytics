# Decisions: Plan Review Responses

**Feature**: `003-dbt-transforms`

## Round 1 (reviews/plan-review-1.md)

All 10 findings are FIXED; none is rebutted. Each pointed to a real gap. For P1-1 the reviewer left
the mechanism open. I chose to document the failed-build state and test same-database recovery,
rather than build machinery that publishes all marts at once. The reasons are below and in
research.md R18.

Ruling R-A (mart indexes in dbt config satisfy FR-010) is recorded in spec.md Assumptions as
requested (one line, a clarification only).

Task IDs T001–T037 are unchanged; the new checks were added inside existing tasks. The dbt node
count is unchanged at 75: the new checks extend existing singular tests and add no new nodes. Mutation
cases go from 6 to 9. dbt invocations in the suite go from 7 to 8.

### P1-1: "Safe to retry" claimed but not tested; a failed invariant leaves the bad mart published
Finding: the Constitution Check cited two different databases as retry evidence, and the plan never
said what a failed `dbt build` leaves behind. A mart's tests run after it is swapped in, so a mart
that fails its own test stays published while downstream marts keep their old contents.

Response: FIXED

Reasoning: the reviewer is right on both counts. I chose the simplest stance that meets Principle
II: retry must be safe, but the constitution does not require atomic publication. Every model is a
full rebuild from an untouched `public`, so a rerun converges by construction. That is now tested on
the same database:
- `test_build_fails_and_names_failing_test` now has two parts.
  1. A failing build: exit 1, the test is named, and the downstream marts are asserted `skipped`
     from `run_results`.
  2. The unmodified project on the **same** database: exit 0, `analytics_snapshot` equal to the
     session build's, and `public` (including `alembic_version`) unchanged.
- The intermediate state is now stated in research.md R18, in contracts/transform.md ("State after
  a non-zero exit") and in README task T035. After a non-zero exit, the analytics schemas are not
  trustworthy until the next exit-0 run, and the rerun is the recovery. Phase 4 orchestration will
  gate consumers on the exit code.
- The Constitution Check cell now names this test.
- R5's invocation count is now 8.

Alternatives I rejected (R18):
- `--fail-fast`: the failing mart is already published, so it only changes how many marts are
  stale.
- A temporary schema plus a schema swap: needs custom schema macros and `ALTER SCHEMA … RENAME`
  outside dbt's materializations (hooks or run-operations, which R4 forbids). Two renames leave a
  window with no schema at all. That is more DDL code than the Phase 3 risk justifies, and the
  marts have no automated consumer before Phase 4.

### P1-2: "No violations" contradicts "one justified deviation"; FK rationale wrong
Finding: Complexity Tracking said "No constitution violations" while the check spoke of a deviation.
The missing database FKs were justified with "FKs to views are impossible", which is wrong, since a
mart could reference `public`.

Response: FIXED

Reasoning: agreed; the stated FK reason was incorrect. Complexity Tracking now opens with two
explicitly justified items:
1. dbt-owned indexes and keys are defined in model config (an interpretation; ruling R-A).
2. There are no database FKs on marts.

The FK rationale is corrected:
- An FK from a mart to `public` would make ingestion's DML depend on analytics. 002's convergence
  `DELETE` of removed matches would fail, or cascade into analytics, which breaks R4's ownership
  split and the spirit of FR-007.
- FKs between marts cannot survive dbt's tmp-table swap: the referenced table is renamed and
  dropped during a rebuild.
- `relationships` tests are the substitute in both cases.

The Constitution Check result line and the "Tech" row now point to both items. spec.md Assumptions
gained the one-line FR-010 clarification exactly as required.

### P1-3: Real-data invariants miss per-side values and derived ratios
Finding: a swapped unpivot of goals, swapped home/away shares, or wrong goal ratios would pass every
test that runs on real data. Only the fixture would catch them.

Response: FIXED

Reasoning: correct. On real data (SC-003) only the singular tests run, so they must cover every
derived column. Changes in data-model.md §4:
- `assert_team_form_two_rows_per_match` now compares `goals_for`, `goals_against`, `result`,
  `points`, `league_code`, `season` and `match_date` of each side with `stg_matches`.
- The home-advantage reconcile test now also recomputes the three shares.
- The goals reconcile test now also recomputes `goals_per_match`, `over_2_5_share` and `btts_share`.
- Three mutation cases were added: `team_form_goals_swapped`, `home_away_shares_swapped`,
  `btts_share_off` (+0.0001, the smallest representable change). That gives 9 cases.
  `test_singular_tests_cover_every_file` now requires at least one case per test, not exactly one.

### P1-4: Explain-script test asserts plan nodes that are coincidental at fixture size
Finding: at 30 rows the planner may pick a seq scan even with the index present. Asserting
`Index Scan` depends on uncontrolled statistics.

Response: FIXED

Reasoning: agreed. My own probe measured plan shapes only on the full data. T024 now asserts
structure only:
- exit 0;
- the six `\echo Q1…Q6` labels are present;
- `Execution Time:` lines = 5 × 11 variants;
- both catalog snapshots are unchanged.

T026 requires the labels and a header listing the variants, and forbids planner overrides.
data-model.md §6 states the variant counts.

### P1-5: Key/relationship test can pass vacuously; T010's "expected failure" is not one
Finding: skipping models "not yet built" means a renamed or dropped mart would be skipped rather
than failing the test. An empty project also builds with exit 0.

Response: FIXED

Reasoning: correct.
- T010 now introduces a literal `EXPECTED_MODELS`, used by both
  `test_build_succeeds_and_runs_every_node` and `test_every_model_has_key_and_relationship_tests`,
  with literal key and FK dicts. Nothing is skipped.
- T018 extends the literal to all 7 models before the mart SQL is written, so those tests fail
  first, as test-first requires.
- T010's expected failure is now "model names missing from `EXPECTED_MODELS` / missing relations".
  It says explicitly that an empty build exits 0.

### P1-6: Failing-test injection under-specified; quickstart inconsistent
Finding: "append a `data_tests` entry" to YAML either fails to parse (exit 2, not 1) or does not
attach to the column. The quickstart injected a different test (`mart_team_form.points`).

Response: FIXED

Reasoning: correct; appending to YAML cannot express the edit.
- T010 now specifies a `yaml.safe_load` → append `"unique"` to `stg_matches.home_points.data_tests`
  → `yaml.safe_dump` round trip.
- quickstart §3 uses the same file, column and test name (`unique_stg_matches_home_points`). It
  warns that a second `models:` entry is a parse error, and expects the four marts to be `SKIP`
  followed by a recovering rerun.
- The skipped-downstream assertion is part of the test (P1-1).

### P1-7: Subprocess hygiene (cwd, tracking, deprecations, compiled-SQL wrapping)
Finding:
1. dbt runs in the repository root.
2. No-tracking rests only on the project flag.
3. The no-deprecation rule is untested.
4. The `SELECT count(*) FROM (<sql>) t` wrapper breaks on a trailing `--` comment or a `;`.

Response: FIXED

Reasoning: all four are cheap and remove real failure modes.
1. `run_dbt` uses `cwd` = the run's temporary dir (data-model.md §5, T007).
2. `dbt_env` adds `DBT_SEND_ANONYMOUS_USAGE_STATS=false`, asserted in T006, and the transform image
   sets the same ENV (T029).
3. `test_build_succeeds_and_runs_every_node` asserts `"deprecat"` is absent from stdout.
4. The wrapper is `SELECT count(*) FROM (\n{sql}\n) t` (data-model.md §5, T016), and
   `test_models_use_no_volatile_functions` (T002) also rejects `;` in singular test files.

### P1-8: FR-007/SC-004 gaps: `alembic_version` and real-data hash of one model only
Finding: the public snapshot omitted `alembic_version`, and the real-data identity check hashed only
`mart_team_form`.

Response: FIXED

Reasoning: correct.
- The public state is now the triple (snapshot, catalog, `alembic_version` rows), defined as
  `helpers.public_state`. It is used by the session fixture, by
  `test_public_tables_unchanged_by_build`, by `test_second_build_is_identical` and by the recovery
  test.
- quickstart §1 computes one md5 per model for all 7, ordered by each model's key, after each build.
  T036 records that all 7 match.

### P1-9: Cross-artifact inconsistencies
Finding: the `analytics_snapshot` signature differed between data-model.md and T008. The
`H`/`A` → boolean mapping of `is_home` was unstated. SOURCES.txt provenance was inconsistent with
research.md.

Response: FIXED

Reasoning:
- `analytics_snapshot(conn, models=tuple(ANALYTICS_MODELS))` with a fixed list is now in both
  artifacts, consistent with P1-5.
- T005 states `H` → `true`, `A` → `false` (a JSON boolean).
- SOURCES.txt will say the values were computed by a separate plain-Python script with
  `ROUND_HALF_UP` and cross-checked independently by the reviewer. That is what happened.

### P1-10: Brittle configuration-mirroring unit tests
Finding: exact-string assertions on pins and Compose values fail on harmless edits.

Response: FIXED

Reasoning: the reviewer marked this optional, but the point is right: these tests should guard
behaviour, not text.
- `test_dbt_pinned_to_1_11` now parses the requirements and asserts that `1.12.0` is excluded and
  a `1.11.x` admitted. That is the property R1 actually depends on.
- `test_compose_transform_service_uses_env_only` now asserts properties:
  - `target: transform`;
  - the profile is present;
  - depends on `migrate`, not `ingest`;
  - no `env_file`;
  - the environment contains the variable names read from `profiles.yml`;
  - every value is a `${POSTGRES_*}` reference or a credential-free literal.
- The Dockerfile test keeps its structural checks (`--no-default-groups`, `--only-group transform`,
  `app` last). Those are the behaviour-bearing facts of R2.
