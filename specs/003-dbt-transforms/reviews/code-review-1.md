# Code Review — Round 1

**Feature**: `003-dbt-transforms` | **Branch**: `003-dbt-transforms` | **Diff**: `origin/main..HEAD`
(spec commit `b99d818`, implementation `fb84db3`..`11f6c77`)

Verdict: REVISE

Counts: 0 BLOCKER, 1 MAJOR, 5 MINOR

## Gate (run by the reviewer)

- `scripts/check.sh`: **exit 0**. ruff format/check clean, `mypy --strict` clean (55 files),
  193 unit tests and 160 integration tests passed.
- `scripts/smoke_compose.sh`: **exit 0**. Both images built, transform ran twice
  (`PASS=75 WARN=0 ERROR=0 SKIP=0`, mart counts `24|2|2|6` both times), image separation held.
- Independent check of I-2: `uv run --frozen --with mypy==2.4.0 --with pathspec==1.1.1 python -m mypy src tests`
  reports "Success: no issues found in 55 source files". The current code passes the stricter
  checker as well.
- Independent check of the fixture: I recomputed every `mart_team_form` row from
  `matches_transform.csv` with a separate script (sort by `(date, source row)`, previous ≤ 5,
  null sums on the first match). All 30 rows equal `matches_transform.expected.json`. The
  share/average/goal expectations also check out by hand (for example SP1 2021/22 is 3 × 0.3333 = 0.9999,
  and the Boca lowest-Elo tie goes to 2022-02-05).

## Requirement → evidence

| Req | Proving test(s) | Strength |
|---|---|---|
| FR-001 | `test_models_live_in_analytics_schemas`, `test_manifest_has_no_hooks_and_only_analytics_schemas`, `test_models_layered_staging_and_marts` | strong |
| FR-002 | `test_staging_derivations_match_expected` (exact rows); `accepted_values` | strong |
| FR-003 | `test_season_label_rule` (06-30/07-01, 12-31/01-01, century wrap); fixture rows 2/3 | strong |
| FR-004 / SC-002 | four whole-list equality tests via `mart_rows`, every column compared | strong |
| FR-005 | `test_same_date_matches_ordered_by_match_id`, `test_form_window_capped_at_five_previous`, `test_first_match_has_empty_form`, exact team-form rows, LAG invariant | strong |
| FR-006 | `test_every_model_has_key_and_relationship_tests` (literal dicts, nothing skipped); 9 mutation cases | strong |
| FR-007 / SC-004 | `test_second_build_is_identical` (rows + catalog + `public` + `alembic_version`), `test_public_tables_unchanged_by_build` | strong |
| FR-008 | `test_profile_reads_only_env_vars`, `test_compose_transform_service_uses_env_only`, `test_build_without_credentials_exits_2`, smoke test | strong |
| FR-009 | all of the above run in `check.sh`, and CI runs `check.sh` | strong |
| FR-010 / SC-005 | `test_team_form_has_lookup_index`, `test_explain_script_leaves_catalog_unchanged`, `test_readme_documents_transform`, README table | adequate (see 4) |
| SC-001 / SC-003 | README measurement (manual T036); `test_build_succeeds_on_real_sample` as a proxy | adequate |
| Principle II retry | `test_build_fails_and_names_failing_test` | **weak** (see 1) |
| Edge: empty DB | `test_build_on_empty_database_succeeds` | weak (see 2) |

SQL correctness, checked line by line:
- **Season rule**: `month >= 7`. The `lpad(((y+1) % 100)::text, 2, '0')` gives `1999/00`.
- **Window**: `ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING` over `(match_date, match_id)`. A total order,
  strictly previous rows, `count(*)` 0 and `sum` NULL on an empty frame.
- **Unpivot**: home and away perspectives are mirror images. The two-rows invariant checks every
  per-side value.
- **Shares**: `round(x::numeric/n, 4)::numeric(5,4)`, which rounds half away from zero (the same as
  ROUND_HALF_UP for positive values). The tolerance of 0.00015 is the exact 3 × 0.00005 bound. Division
  by zero cannot happen because every group has n ≥ 1.
- **Elo**: three `row_number()` windows with the documented tie orders. Ingestion guarantees
  finite values (`is_finite()` in `clean._decimal`), so NaN ordering cannot occur. Unrated teams
  come through as nulls via `LEFT JOIN` + `coalesce(rated_matches, 0)`.
- **Empty DB**: a `GROUP BY` over an empty input gives 0 rows, so every model builds empty.

No SQL injection risk: the only f-string SQL in the helpers interpolates module constants. I found no
resource leaks: connections use context managers, and the subprocess uses `run(..., timeout)`.

## Rulings on implementer deviations

- **I-1 (recovery test starts from a built DB): ACCEPT** the added first build. The underlying
  concern from plan-review-2 is not resolved, though. The test still cannot fail on the property it
  claims, so the point is upheld with a sharper argument as finding 1.
- **I-2 (lock moved mypy 2.4.0 → 1.19.1, dropped ast-serialize): ACCEPT.** I verified the cause.
  `dbt-core 1.11.16` declares `pathspec>=0.9,<0.13`, and every `mypy>=2.0.0` declares
  `pathspec>=1.0.0`. `uv pip compile` with `mypy>=2` plus `dbt-core>=1.11.15,<1.12` is unsatisfiable,
  and without the `mypy>=2` constraint it resolves to `mypy 1.19.1` + `pathspec 0.12.1`.
  `ast-serialize` is a mypy-2-only dependency. Declaring the groups as conflicting is not an option,
  because the tests run `dbt` from the same venv as pytest/mypy (`sys.executable` sibling).
  Does this silently lower the gate? Not for the current code, which passes mypy 2.4.0 `--strict`
  (verified above). It is still a silent, unrecorded constraint in `pyproject.toml`, which leads
  to finding 3.
- **I-3 (Q3 "before" 3.240 ms vs research 9.3 ms): ACCEPT.** The plan shape and the verdict are the
  same, the candidate is still rejected, and the README and decisions.md agree on 3.240 / 0.057.
- **I-4 (two PKs measured ad hoc): ACCEPT** the method. The README numbers match decisions.md
  (0.030 → 0.025, 0.035 → 0.014). The queries themselves are not recorded anywhere, though, so the
  rows cannot be reproduced (finding 4).
- **I-5 (README written once with final numbers): ACCEPT.** No placeholder was ever committed, and the
  test forbids `TBD`.
- **I-6 (`DbtResult.target_dir` is the artifact dir): ACCEPT.** One consistent meaning, and the
  behaviour is unchanged.

## Findings

### 1. MAJOR: the recovery test cannot detect a rerun that fails to converge
`tests/integration/test_transform_build.py:229-234`

**Problem.** The failing build re-creates the staging views (same definition) and skips the four
marts. The marts keep the first build's tables, which already equal `transformed_db`'s. So
immediately after the failing build, `analytics_snapshot(conn) == analytics_snapshot(reference)`
already holds, and the "recovery" assertion is true before the rerun even starts. A rerun that
rebuilt nothing would still pass. One example is a mart switched to `materialized: incremental`, or
`+enabled`/selector drift that leaves a node out; `returncode == 0` alone does not prove every node
ran. This test is the evidence the plan's Constitution Check cites for Principle II ("safe to retry
after a partial failure"), and R18's claim "every model is a full rebuild, so the rerun converges"
is untested.

**Required change.** Between the failing build and the rerun, put the analytics schemas into a
genuinely partial state that only a full rebuild repairs, for example
`DELETE FROM analytics_marts.mart_team_form WHERE team_match_number > 2` plus
`UPDATE analytics_marts.mart_team_ratings SET peak_elo = 0`, or `DROP TABLE` one mart. Assert that
the snapshot now differs from the reference, then rerun and assert that it is equal again. Also assert
that the rerun's `statuses()` covers `EXPECTED_MODELS` with `success` and every test with `pass`.

### 2. MINOR: the empty-database test passes vacuously for a missing model
`tests/integration/test_transform_build.py:244-247`

**Problem.** `if exists[0] is None: continue` skips any model that was not created, and
`assert exists is not None` is always true (`fetchone()` of `SELECT to_regclass(...)` always returns
a row). On an empty, migrated database every model must exist; that is the spec edge case
"build succeeds and marts are empty". Other tests catch a missing model on the fixture DB, so this
is not MAJOR. But this is the same vacuous-pass pattern plan review P1-5 removed elsewhere.

**Required change.** Assert `exists[0] is not None` for every entry of `ANALYTICS_MODELS`, drop the
`continue`, and assert `set(result.statuses().values()) <= {"success", "pass"}`.

### 3. MINOR: make the mypy cap explicit (I-2)
`pyproject.toml:24`

**Problem.** `"mypy"` is unconstrained. The cap at `<2` exists only as an emergent effect of dbt's
`pathspec<0.13` pin. Someone reading `pyproject.toml` cannot tell that the gate runs on a type checker
one major version back. If dbt relaxes the pin, a later `uv lock --upgrade` moves the checker silently.

**Required change.** Declare `"mypy>=1.19.1,<2"` with a one-line comment
(`# capped by dbt-core 1.11's pathspec<0.13; mypy>=2 needs pathspec>=1 (003 decisions I-2)`), and
re-lock. Keep the note in decisions.md.

### 4. MINOR: the ad hoc PK measurements cannot be reproduced
`README.md:321,327,329`; `specs/003-dbt-transforms/decisions.md` I-4

**Problem.** The README says "to re-measure, run the script". The two ad hoc rows
(`mart_team_form` PK, `mart_league_season_goals` PK) are not in the script, and neither the README nor
decisions.md records the query text or the drop statement used. The constitution requires performance
claims to be measured *and recorded*, and the method is only half recorded.

**Required change.** Record the exact SQL of both ad hoc variants: the `BEGIN; ALTER TABLE … DROP
CONSTRAINT …; EXPLAIN (ANALYZE, BUFFERS) …; ROLLBACK;` block and the literal key values. Put it
in decisions.md I-4 or in a fenced block in the README that contains `ROLLBACK`, which the README test
allows. Growing the script is not required.

### 5. MINOR: the real-data team-form invariant misses `season_start_year`; `is_home` has no `not_null`
`transform/tests/assert_team_form_two_rows_per_match.sql:85-90`; `transform/models/marts/_marts.yml:54-55`

**Problem.** Plan review P1-3 required the singular tests to cover every derived column on real
data. The two-rows test compares `league_code`, `season` and `match_date` but not `season_start_year`.
A row with `is_home` NULL falls into neither `home_side` nor `away_side`, so an extra NULL-`is_home`
row for a third team_id would go unnoticed. That row cannot come from the current SQL, but the
invariant is meant to be independent of the SQL.

**Required change.** Add `season_start_year` to the home/away aggregates and comparisons. Add a
`not_null` test (or a contract `not_null` constraint) on `mart_team_form.is_home`, or make the
invariant count rows per `match_id` overall (`count(*) <> 2` fails). Update the T023 node count
in the README if a node is added.

### 6. MINOR: `DbtResult.manifest()` typing forces four `type: ignore`s, one without a reason
`tests/integration/dbt_runner.py:63`; `tests/integration/test_transform_build.py:75,133,135`;
`tests/integration/test_transform_idempotency.py:43`

**Problem.** `manifest()` returns `dict[str, object]`, so every caller has to suppress a type error.
Line 135 (`sources`) has no reason comment. Suppressions that exist only to undo a too-narrow return
type weaken the strict gate for no benefit.

**Required change.** Have `manifest()` return `dict[str, Any]` (it is untyped JSON), or add a
small typed accessor such as `nodes()` / `sources()`, and remove the four ignores.

## Not raised (checked and fine)

- App image stays dbt-free (`--no-default-groups` in both `app` syncs; the smoke test asserts that
  `import dbt` fails). The transform image is `--only-group transform` and non-root, with artifacts in
  `/tmp/dbt`, tracking off, and `import football_stats` failing.
- `profiles.yml` contains only `env_var()` calls with no defaults or literals. `.env.example` has only
  placeholders. No data files are committed.
- CI runs `check.sh` (which builds and tests dbt against testcontainers PostgreSQL), both image
  builds, and the smoke test.
- dbt runs in a temp `cwd` with `HOME`/target/log in temp dirs, an explicit env, and
  `DBT_SEND_ANONYMOUS_USAGE_STATS=false` plus the project flag. `partial_parse: false`. No
  skip/xfail markers were added. The randomized order uses a fixed seed. Mutation cases roll back and
  re-assert 0 rows.
- The `transform` service is profile-gated like `ingest`. That matches the 001 precedent and the
  spec's "orchestration is Phase 4" assumption, so it is no `docker compose up` regression.
- README numbers (75 nodes = 7 + 21 + 41 + 6, 474,946 rows, the speedup of about 300 times = 10.001 / 0.033)
  agree with decisions.md and the smoke output.
