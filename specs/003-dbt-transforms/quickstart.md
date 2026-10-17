# Quickstart & Validation Guide: Analytics Transforms

**Feature**: `003-dbt-transforms` | Contracts: [transform.md](./contracts/transform.md),
[marts.md](./contracts/marts.md) | Models: [data-model.md](./data-model.md)

Everything that can be automated is in the quality gate. Steps marked **(manual)** need the real
dataset or a stopwatch. Task T036 runs them once and writes the results to the README. The expected
numbers are the planning profile (research.md R14, R17). If a measured value differs, **stop and
report the difference** in `decisions.md`; do not edit the expectation to match.

## 0. Quality gate (automated)

```bash
uv sync --frozen            # installs the dev and transform groups (dbt 1.11.x)
scripts/check.sh            # ruff format, ruff check, mypy, unit tests, integration tests (incl. dbt)
scripts/smoke_compose.sh    # builds both images, migrates, ingests a fixture, runs transform twice
```

Expected: both exit 0. `scripts/check.sh` stays under about 60 s (T037 records the time). The
gate covers every FR/SC except the manual items below (plan.md "FR / SC → Test Mapping").

## 1. Full build on the real dataset (SC-001, SC-003, SC-004, US1, US4) **(manual, timed)**

Start from a database with the full ingest (002 state: 237,473 matches). If needed, run
`docker compose run --rm ingest` first.

```bash
docker compose --profile transform build
time docker compose run --rm transform        # first build
time docker compose run --rm transform        # second build (SC-004)
```

Expected: both exit 0 with `Done. PASS=75 WARN=0 ERROR=0 SKIP=0` (SC-003: 0 data-test failures).
Each takes well under 2 min (SC-001; probe: about 5 s wall time). Then check contents and
idempotency:

```bash
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At <<'SQL'
SELECT count(*) FROM analytics_marts.mart_team_form;                         -- 474946
SELECT count(*) FROM analytics_marts.mart_league_season_home_advantage;      -- 789
SELECT count(*), count(*) FILTER (WHERE rated_matches > 0) FROM analytics_marts.mart_team_ratings;  -- 1221 | 783
SELECT matches_played, home_win_share, draw_share, away_win_share
  FROM analytics_marts.mart_league_season_home_advantage
 WHERE league_code = 'E0' AND season = '2015/16';                            -- 380 | 0.4132 | 0.2816 | 0.3053
-- SC-004: one content hash per model (all 7), ordered by its key; record all 7 lines
SELECT 'stg_leagues', md5(string_agg(to_jsonb(t)::text, '' ORDER BY league_code)) FROM analytics_staging.stg_leagues t
UNION ALL SELECT 'stg_teams', md5(string_agg(to_jsonb(t)::text, '' ORDER BY team_id)) FROM analytics_staging.stg_teams t
UNION ALL SELECT 'stg_matches', md5(string_agg(to_jsonb(t)::text, '' ORDER BY match_id)) FROM analytics_staging.stg_matches t
UNION ALL SELECT 'mart_team_form', md5(string_agg(to_jsonb(t)::text, '' ORDER BY match_id, team_id)) FROM analytics_marts.mart_team_form t
UNION ALL SELECT 'mart_league_season_home_advantage', md5(string_agg(to_jsonb(t)::text, '' ORDER BY league_code, season)) FROM analytics_marts.mart_league_season_home_advantage t
UNION ALL SELECT 'mart_league_season_goals', md5(string_agg(to_jsonb(t)::text, '' ORDER BY league_code, season)) FROM analytics_marts.mart_league_season_goals t
UNION ALL SELECT 'mart_team_ratings', md5(string_agg(to_jsonb(t)::text, '' ORDER BY team_id)) FROM analytics_marts.mart_team_ratings t;
SQL
```

Run the hash query after the first and after the second build. All 7 hashes must be the same both
times (SC-004 on real data, 100 % of models; the automated proof is `test_second_build_is_identical`).
T036 records that all 7 matched. Also confirm that
`public` is untouched: `SELECT count(*) FROM matches` still prints 237473, and
`SELECT version_num FROM alembic_version` prints `0003_contract_constraints`.

## 2. Index measurements (FR-010, SC-005, US3) **(manual)**

```bash
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -f - < scripts/explain_transforms.sql > /tmp/explain_transforms.txt
```

The script changes nothing persistent: every "before" or candidate index lives inside
`BEGIN … ROLLBACK` (data-model.md §6). Afterwards, check that
`\di analytics_marts.*` still lists the team-form index and the four primary keys, and that
`public` has no new index.

From the 5 runs per query, take the median "Execution Time" and record it in the README table
"Measured numbers - analytics transforms": index, query, before (ms), after (ms), plan node before
and after. Expected shape (research.md R14): the team-form lookup goes from a parallel seq scan of
about 7–10 ms to an index scan of about 0.02 ms. `matches(match_date)` does not change the
staging/mart source scan; it only speeds up the cross-league date range, which no model runs, so it
is not added (record it as a negative result). `(league_code, match_date)` is redundant with
`matches_natural_key`. If a rejected candidate speeds up a staging/mart source query by more than
20 %, stop and record a plan deviation in `decisions.md`.

## 3. Failure is loud (US1-AS3) **(automated; optional manual look)**

`test_build_fails_and_names_failing_test` adds a deliberately failing test to a copy of the project,
then shows that a rerun of the unmodified project on the same database recovers (research.md R18).
To see it by hand: copy `transform/` to `/tmp/transform`; in `/tmp/transform/models/staging/_staging.yml`,
under model `stg_matches`, column `home_points`, add `unique` to that column's existing `data_tests`
list (edit the list; do not append a new `models:` entry, which is a parse error with exit 2). Run the
host command with `--project-dir /tmp/transform --profiles-dir /tmp/transform`. Expected: exit 1,
`FAIL … unique_stg_matches_home_points`, and the four marts reported as `SKIP`. Then run the normal
host command: exit 0, and the marts are correct again.

## 4. CI (US4-AS2)

CI runs `uv sync --frozen`, `scripts/check.sh` (which includes the dbt integration tests against
testcontainers PostgreSQL), the app image build, the transform image build
(`docker build --target transform .`) and `scripts/smoke_compose.sh`. Expected: all green on the
pull request.
