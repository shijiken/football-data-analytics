# Plan Review — 003-dbt-transforms, round 1

**Reviewed commit**: 1b00548 (branch `003-dbt-transforms`)
**Scope**: spec.md, plan.md, research.md, data-model.md, contracts/transform.md, contracts/marts.md,
quickstart.md, tasks.md, against constitution 1.1.0 and the implemented 001/002 code (`Dockerfile`,
`docker-compose.yml`, `scripts/check.sh`, `scripts/smoke_compose.sh`, `.github/workflows/ci.yml`,
`pyproject.toml`, `migrations/versions/0001–0003`, `src/football_stats/load.py`, `data_contract.py`,
`tests/integration/conftest.py`, `helpers.py`, `tests/stages.py`). There is no `decisions.md` yet,
so there are no rebuttals to rule on.

Verdict: REVISE

There are 2 MAJOR findings, both about constitution compliance that the plan claims but does not
show, and 8 MINOR findings. The core design is sound: the models, window frames, fixture, test
harness, Docker split and gate determinism all hold up. The fixes are small.

---

## What I verified independently

**Fixture expectations (plan.md "Test Fixtures")**: I recomputed every expected value with a
separate Python script, using the plan's row table and `ROUND_HALF_UP` on `Decimal`. I did not
look at the planner's numbers while doing it. **0 differences** in any of:
- `stg_matches`, all 15 rows. Season boundaries: 2021-06-30 → `2020/21`, 2021-07-01 → `2021/22`,
  ARG 2022-02-05 → `2021/22`, ARG 2022-08-06 → `2022/23`. Per-side results, points, total goals.
- `mart_team_form`, all 30 rows. Points:
  - Arsenal row 5 (same date as row 4, higher `match_id`) has form 4 matches, 8 pts, GF 6, GA 2,
    so row 4 is included.
  - Arsenal row 7 (window = rows 2–6) has 8 pts.
  - Arsenal row 9 (window = rows 3–7) has 7 pts, GF 8, GA 6.
  - Every first match has `form_matches` 0 and null sums.
- Both league-season marts, all 5 + 5 rows: 3/7 → 0.4286, 2/7 → 0.2857, 13/7 → 1.8571,
  23/7 → 3.2857, 5/7 → 0.7143. SP1 shares sum to 0.9999. ARG zero shares are `0.0000`.
- `mart_team_ratings`, all 9 rows:
  - Arsenal peak tie 1650.0 on 2021-08-14 and 2021-09-18 resolves to the earliest date.
  - Boca peak/lowest tie 1700.0 resolves to 2022-02-05, while latest is 2022-08-06.
  - Leeds' latest *rated* match is 2021-09-11 (row 9 has no Elo).
  - Wolves has 1 rated match. Four teams are unrated with nulls.
- Row 16 (`1-0 A`) violates `ft_result_consistent` (checked in `data_contract.py`), so it is
  excluded. Its 1999.0 Elo cannot leak into any mart.

**Window and unpivot**: `ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING`, partitioned by `team_id` and
ordered by `(match_date, match_id)`, is correct. It covers strictly earlier matches only, at most 5,
in a total order, and `sum` over an empty frame gives null. The unpivot is a `UNION ALL` of the
home and away perspectives. The public `CHECK (home_team_id <> away_team_id)` (0001 line 86) makes
the PK `(match_id, team_id)` sound. The `match_id = source_row_number` claim holds: `load.py` line 112
inserts `ORDER BY s.source_row_number` into a fresh identity column. Test count: staging 21 +
marts 41 + singular 6 + 7 models = 75 nodes, which matches the plan.

**dbt versions**: PyPI confirms 1.12.6 is the latest release and that it requires
`dbt-core-experimental-parser>=2.0.0b1`. 1.11.16 does not, so the `<1.12` pin rationale (R1) holds.

**Docker**: `uv sync --help` (uv 0.12.5) confirms that `--no-default-groups` ignores *all* default
groups (dev and transform). `--only-group transform` installs only that group, without the
project. So the app image stays dbt-free and the transform image stays polars-free. The smoke test
asserts both directions. `app` stays the last stage, so the existing `build: .` services and
`docker build .` still build the app. BuildKit, the default on ubuntu-24.04 and Docker 29, skips the
unused `transform` stage there. CI gains one `--target transform` build.

**Planner behaviour at fixture size**: I tested this on `postgres:17.6-alpine` with a scratch
container. With 30 rows and the `(team_id, match_date, match_id)` index present, the team-form
lookup plans as `Seq Scan` + `Sort`. A never-analysed 9-row PK table plans as `Index Scan`. This
matters for finding 4.

---

## Rulings requested by the lead

**R-A. FR-010 / no Alembic 0004 / mart index in dbt config: ACCEPTED as compliant, with a spec
clarification.**

- FR-010 says "versioned structure change", not "migration". A `config(indexes=…)` in a
  git-tracked model file meets that wording: every `dbt build` applies it from the repository, it
  is never applied by hand, and it cannot drift from the code.
- An Alembic migration *cannot* own a structure that dbt drops and recreates on every run. The
  index would vanish on the next build, and a migration that touches `analytics_marts` would make
  Alembic depend on dbt having run.
- The constitution's "Transforms: analytical models live in SQL (dbt …)" assigns ownership of
  these schemas to dbt. Its "Schema changes MUST be made through versioned migrations" is
  satisfied in spirit for dbt-owned schemas and literally for `public`.
- Skipping 0004 is justified by measurement (R14). The planned `matches(match_date)` index does not
  change the full-scan plans of the models. `(league_code, match_date)` is a prefix of
  `matches_natural_key`.
- What I do require is that the interpretation is written down once, where future features will
  find it. See finding 2.

**R-B. FR-007 proof**: adequate in design. Public is compared by `helpers.snapshot` and
`catalog_snapshot` before and after the build. The manifest is checked for no hooks, no operations
and sources only in `public`. Two builds are compared by full row content (`to_jsonb` ordered by
key) for all 7 models, plus the analytics catalog with generated names stripped. That compares
content, not counts. Two small gaps remain: `alembic_version` contents, and the real-data md5,
which covers only one model (finding 8).

---

## Findings

### 1. MAJOR — Principle II "safe to retry after a partial failure" is claimed but not tested; a failed invariant leaves the bad mart published

**Location**: plan.md Constitution Check row "II. Safe to retry"; research.md R5 and R16;
tasks.md T010 (`test_build_fails_and_names_failing_test`).

**Problem**: the evidence cited is "`test_build_fails_and_names_failing_test` followed by a passing
build in `test_second_build_is_identical` (fresh DB)". Those are two different databases. Nothing
shows that a build *on the database a failed build left behind* recovers to the correct state. The
plan also never states what a failed build leaves behind. In `dbt build` a mart's data tests run
*after* its table has been swapped in, so a mart whose singular invariant fails stays published.
Its downstream nodes are skipped, so `analytics_marts` can then hold tables from two different
runs. The exit code is loud, but a Phase 5 consumer reading the table would not know. This is a
constitution MUST (Principle II, bullet 3), and the Constitution Check marks it PASS without
evidence.

**Required change**:
- Extend `test_build_fails_and_names_failing_test`, or add `test_build_recovers_after_failed_build`.
  After the failing build (exit 1, with skipped downstream nodes asserted from `run_results`), run
  the *unmodified* project on the same database. Assert exit 0, then assert that
  `analytics_snapshot` equals the snapshot of the session build `transformed_db`, and that the
  `public` snapshot is unchanged. This adds about 3 s and one dbt invocation; update the
  "7 invocations" count in R5.
- Fix the Constitution Check evidence cell to name that test.
- In research.md R16 (or a new short R-entry), contracts/transform.md "Exit codes" and the README
  task T035, state this: on exit 1, marts built before the failing test are already replaced and
  downstream marts keep their previous contents, so consumers must treat a non-zero transform as
  "marts not trustworthy" until a successful rerun. Phase 4 gating is the mitigation.

### 2. MAJOR — Constitution Check says "no violations", but two schema-design deviations are undocumented or mis-justified

**Location**: plan.md Constitution Check row "Tech: versioned migrations, PKs/FKs/indexes";
"Result … The one justified deviation …"; Complexity Tracking ("No constitution violations").

**Problem**:
- The plan contradicts itself. The Constitution Check result line speaks of "one justified
  deviation", while Complexity Tracking says "No constitution violations".
- Governance requires every deviation to be justified in Complexity Tracking. Two exist:
  1. Mart indexes and keys are defined in dbt config rather than "versioned migrations (Alembic or
     ordered `.sql` files)".
  2. Marts have no database foreign keys, although "tables MUST declare primary keys, foreign
     keys".
- The FK justification is wrong. It says "DB FKs to views are impossible", but a mart could
  reference `public.teams` and `public.matches`. dbt contracts support `foreign_key` constraints.
- The real reason is stronger and should be recorded. An FK from `mart_team_form.match_id` to
  `public.matches` would make ingestion's `DELETE` of removed matches (002 `matches_removed`) fail
  or cascade into analytics. Ingestion would then depend on analytics, which breaks the
  ownership split of R4 and the spirit of FR-007.

**Required change**:
- Change Complexity Tracking to list both items as justified constitution deviations or
  interpretations, with the corrected FK rationale: FKs to `public` would couple ingestion DML to
  analytics, and FKs between marts are blocked by the per-table swap. In each case the
  `relationships` tests are the substitute.
- Make the Constitution Check result line consistent with it.
- Add one line to spec.md Assumptions recording ruling R-A, so the next feature inherits it: "For
  dbt-owned schemas, the dbt model definition (including its `indexes` and contract constraints)
  is the versioned structure change; schema changes in `public` remain Alembic migrations."
- This is a spec clarification, not a requirement change, so no re-review of the stories is
  needed.

### 3. MINOR — Real-data invariants do not cover per-side values or derived ratios

**Location**: data-model.md §4.

**Problem**: on the real dataset (SC-003) only the singular tests run, and they leave some mart
columns unchecked:
- `assert_team_form_two_rows_per_match` checks the team ids per side, but not that `goals_for`,
  `goals_against`, `result` and `points` equal the side's values in `stg_matches`. A swapped unpivot
  of goals would pass every real-data test. The lag test recomputes from the mart's own columns.
- The home-advantage reconcile test checks counts and averages but not the three shares.
  Swapping `home_win_share` and `away_win_share` keeps the sum at 1.
- `assert_goals_reconcile_with_staging` does not check `goals_per_match`, `over_2_5_share` or
  `btts_share`.
- The fixture catches all of these. Real data does not.

**Required change**:
- Extend the two-rows test, or the lag test, to join `stg_matches` and compare the per-side
  values.
- Make the two reconcile tests also compare every share and ratio against
  `round(count::numeric / matches_played, 4)` recomputed from staging.
- The existing mutation cases still apply. Adding one mutation for a swapped share would be
  cheap.

### 4. MINOR — `test_explain_script_leaves_catalog_unchanged` asserts plan nodes that are coincidental at fixture size

**Location**: tasks.md T024.

**Problem**: "output contains `Index Scan` and `Seq Scan`". My scratch run shows that, at 30 rows,
the team-form lookup is a Seq Scan even with the index present. `Index Scan` would only appear
because some other never-analysed table (for example a PK lookup on the 9-row ratings table) still
has `relpages = 0`. The assertion therefore depends on catalog statistics that nothing controls,
not on the script. It would also tempt an implementer to add planner overrides to the script.

**Required change**: drop the plan-node assertions. Assert structure instead:
- exit 0;
- each of the 6 query labels (as `\echo` markers) is present;
- the number of `Execution Time:` lines equals (number of variants × 5);
- both catalog snapshots are unchanged.

### 5. MINOR — `test_every_model_has_key_and_relationship_tests` can pass vacuously

**Location**: tasks.md T010 and T023; plan.md FR-006 row.

**Problem**: "models not yet built in the current phase are skipped". After US2 this stays true.
If a mart is renamed or its YAML entry dropped, the test skips it instead of failing. Similarly,
T010's "expected failure: no models" is not a real failure. An empty dbt project builds with
exit 0, and the set-equality checks pass on two empty sets.

**Required change**:
- Key the expected model list on a literal: the 3 staging models in US1, extended to all 7 in
  T019–T022. After T022 no model may be skipped.
- State in T010 that the expected failure is a missing relation or a missing model in the literal
  list, not an empty build.

### 6. MINOR — The failing-test injection is under-specified and inconsistent with the quickstart

**Location**: tasks.md T010 (`test_build_fails_and_names_failing_test`); quickstart.md §3.

**Problem**:
- "Append to the copy's `_staging.yml` a `data_tests: [unique]` entry on `stg_matches.home_points`"
  cannot be done by appending text. A second `models:` entry for `stg_matches` is a dbt parse
  error, which exits 2, not 1. A YAML key appended at the end does not attach to the column.
- quickstart §3 injects into `models/marts/_marts.yml` on `points` and expects
  `unique_mart_team_form_points`, which disagrees with T010.

**Required change**:
- Specify the edit as a YAML round trip: `yaml.safe_load`, then add `{"unique": …}` to the
  `data_tests` of column `home_points` of model `stg_matches`, then `yaml.safe_dump`.
- Align quickstart §3 with T010 (same file, column and test name).
- Have the test also assert that the build's downstream mart nodes are `skipped`, which feeds
  finding 1.

### 7. MINOR — Subprocess hygiene: working directory, tracking belt-and-braces, deprecation rule, compiled-SQL wrapping

**Location**: data-model.md §5 `run_dbt` and `dbt_env`; tasks.md global rules and T016; Dockerfile
plan (T029).

**Problem**:
1. `run_dbt` passes no `cwd`, so dbt runs in the pytest working directory, the repository root.
   Every path dbt is *known* to write is redirected, but nothing asserts that the repo stays
   clean.
2. No-tracking rests on one source, the `flags:` block in `dbt_project.yml`. The probe confirmed
   it works, but the subprocess env and the image do not reinforce it.
3. The global rule "a build must print no deprecation warnings" has no test.
4. The mutation test wraps `compiled_code` as `SELECT count(*) FROM (<sql>) t`. A singular test
   file that ends in a `--` comment without a trailing newline, or ends in `;`, breaks the wrapper.

**Required change**:
- `run_dbt` uses `cwd=` the target dir.
- `dbt_env` adds `DBT_SEND_ANONYMOUS_USAGE_STATS=false`, and the transform image sets the same ENV.
  Update `test_dbt_env_parses_url_and_passes_nothing_else`.
- `test_build_succeeds_and_runs_every_node` asserts `"deprecat" not in stdout.lower()`.
- Wrap as `SELECT count(*) FROM (\n{sql}\n) t`.
- Add a rule (checked in T022) that singular test files contain no `;`.

### 8. MINOR — FR-007/SC-004 proof gaps on public metadata and on real data

**Location**: tasks.md T010 and T011; quickstart.md §1.

**Problem**:
- `helpers.snapshot` does not include `alembic_version`, so "public untouched" omits one
  public table's contents.
- The real-data identity check (quickstart §1) hashes only `mart_team_form`, while SC-004 says
  "100% of models".

**Required change**:
- In `test_public_tables_unchanged_by_build` and `test_second_build_is_identical`, also compare
  `SELECT version_num FROM alembic_version`.
- In quickstart §1, compute the md5 for all 7 models (one `string_agg(to_jsonb(t)::text …)` per
  model, ordered by its key) after each build. T036 records that all 7 match.

### 9. MINOR — Small cross-artifact inconsistencies

**Location**: data-model.md §5 versus tasks.md T008; plan.md team-form table; tasks.md T005;
R5 and R17.

**Problem and required change**:
- `analytics_snapshot(conn)` iterates the fixed `ANALYTICS_MODELS` in data-model.md, but T008 says
  it "reads the model list from a given manifest". Pick one signature, preferably the fixed list,
  which is consistent with finding 5.
- The plan's team-form expectation table shows `home` as `H`/`A`, while the JSON column `is_home`
  is a boolean. State the mapping (`H` → `true`) in T005 so that the JSON is not written with
  strings.
- T005 says the expected values were "computed by hand", while research.md says "computed by a
  separate plain-Python script". Either is fine, but SOURCES.txt should say which.

### 10. MINOR — Brittle configuration-mirroring unit tests

**Location**: tasks.md T028 (`test_compose_transform_service_uses_env_only` asserts exact
`depends_on`, `image` and value strings) and T002 (exact pin strings).

**Problem**: these tests restate the plan's text rather than a behaviour. Any harmless edit, such
as widening the pin to `>=1.11.16` after a security patch, fails the gate. This does not break a
requirement, but it costs maintenance.

**Required change (optional)**: assert the properties that matter. For the compose service:
- env keys come only from `${POSTGRES_*}` or literals;
- no `env_file`;
- `target: transform`;
- depends on `migrate`, not `ingest`.

For the pin: an upper bound `<1.12` on both packages.

---

## Coverage summary

Every FR-001…FR-010 and SC-001…SC-005 maps to at least one task and one named test. The exceptions
are the explicitly manual SC-001, SC-003 (real data), SC-005 numbers and FR-010 measurements, all
of which go through T036 with a recorded procedure. That is acceptable because they need the full
dataset. Every edge case in spec.md has a named test. No task lacks a requirement or story. The
gate (`scripts/check.sh`, unchanged) covers format, lint, types, unit and integration tests. dbt
runs inside the integration step against testcontainers with no packages, no `dbt deps`, tracking
off, isolated `HOME`, target and log paths, and fixed `started_at`. Expected added time is about
25 s.

## Counts

BLOCKER 0 · MAJOR 2 · MINOR 8
