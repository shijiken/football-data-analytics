# Data Model: Analytics Transforms

**Feature**: `003-dbt-transforms` | **Plan**: [plan.md](./plan.md) | Decisions:
[research.md](./research.md) | Consumer-facing description: [contracts/marts.md](./contracts/marts.md)

No change to the ingestion schema: `public` stays at Alembic head `0003_contract_constraints`
(research.md R14). Everything below is created by `dbt build` in two schemas that dbt owns.

```text
public (Alembic, read-only for dbt)          analytics_staging (views)         analytics_marts (tables)
  leagues  ──source──►  stg_leagues  ───────────────────────────────┬──► mart_league_season_home_advantage
  teams    ──source──►  stg_teams    ──────────────┬────────────────┼──► mart_league_season_goals
  matches  ──source──►  stg_matches  ──────────────┼──► mart_team_form (team-match grain + prev-5 form)
                                                   └──► mart_team_ratings (one row per team)
```

dbt project: `transform/`, project name `football_analytics`, profile `football_analytics`, target
schema `analytics`. The `+schema` values `staging`/`marts` produce `analytics_staging`/
`analytics_marts`. Every model's SQL reads only `source('football', …)` or `ref(…)`.

## 1. Sources (`models/staging/_sources.yml`)

Source `football`, `schema: public`, tables `leagues`, `teams`, `matches`. The columns used are the
ones listed in §2. Nothing else from `public` is read: not `ingest_runs`, not `excluded_rows`.

## 2. Staging (views in `analytics_staging`; light renaming and derivations only, FR-001/FR-002)

### `stg_leagues` — key `league_code`

| Column | Type | Derivation |
|---|---|---|
| `league_code` | text | `leagues.league_code` |

Tests: `unique`, `not_null` (`league_code`).

### `stg_teams` — key `team_id`

| Column | Type | Derivation |
|---|---|---|
| `team_id` | integer | `teams.team_id` |
| `team_key` | text | `teams.team_key` |
| `team_name` | text | `teams.name` |

Tests: `unique` + `not_null` on `team_id` and on `team_key`.

### `stg_matches` — key `match_id`; one row per loaded match

| Column | Type | Derivation |
|---|---|---|
| `match_id` | bigint | `matches.match_id` |
| `league_code` | text | `matches.league_code` |
| `match_date` | date | `matches.match_date` |
| `season_start_year` | integer | `CASE WHEN extract(month FROM match_date) >= 7 THEN extract(year FROM match_date)::integer ELSE extract(year FROM match_date)::integer - 1 END` (FR-003, research.md R7) |
| `season` | text | `season_start_year::text \|\| '/' \|\| lpad(((season_start_year + 1) % 100)::text, 2, '0')`, e.g. `2020/21`, `1999/00` |
| `home_team_id`, `away_team_id` | integer | as in `matches` |
| `home_goals`, `away_goals` | integer | `ft_home_goals::integer`, `ft_away_goals::integer` |
| `total_goals` | integer | `home_goals + away_goals` |
| `ft_result` | char(1) | `matches.ft_result` (`H`/`D`/`A`) |
| `home_result`, `away_result` | text | each side's result: `ft_result` H → home `W`/away `L`; D → `D`/`D`; A → `L`/`W` |
| `home_points`, `away_points` | integer | `W` 3, `D` 1, `L` 0 from the side's result |
| `home_elo`, `away_elo` | double precision | as in `matches` (pre-match Elo, nullable) |

Column order is as listed. Tests: `match_id` `unique` + `not_null`; `league_code` `not_null` +
`relationships → stg_leagues.league_code`; `home_team_id`, `away_team_id` `not_null` +
`relationships → stg_teams.team_id`; `season`, `season_start_year` `not_null`; `ft_result`
`accepted_values [H, D, A]`; `home_result`, `away_result` `accepted_values [W, D, L]`;
`home_points`, `away_points` `accepted_values [0, 1, 3]` (`quote: false`).

## 3. Marts (tables in `analytics_marts`, contract enforced, research.md R11)

Every mart has `config: {contract: {enforced: true}}`. Every column is declared with exactly the
`data_type` below. Key columns have a `not_null` constraint, and each mart has the `primary_key`
constraint shown. `test_mart_columns_match_contract` compares `information_schema.columns`
(`data_type`, `numeric_precision`, `numeric_scale`, column order) with these tables.

### `mart_team_form` — PK `(match_id, team_id)`; two rows per loaded match (FR-004, FR-005)

The team-match unpivot is a CTE: home perspective `UNION ALL` away perspective of `stg_matches`.

| Column | data_type | Derivation |
|---|---|---|
| `match_id` | bigint | |
| `team_id` | integer | home: `home_team_id`; away: `away_team_id` |
| `opponent_team_id` | integer | the other side |
| `league_code` | text | |
| `season` | text | |
| `season_start_year` | integer | |
| `match_date` | date | |
| `is_home` | boolean | `true` for the home perspective |
| `goals_for`, `goals_against` | integer | the side's goals, then the opponent's |
| `result` | text | the side's result (`W`/`D`/`L`) |
| `points` | integer | the side's points |
| `team_match_number` | bigint | `row_number() OVER (PARTITION BY team_id ORDER BY match_date, match_id)` (1-based) |
| `form_matches` | bigint | `count(*) OVER prev5` (0–5) |
| `form_points` | bigint | `sum(points) OVER prev5`; null when `form_matches = 0` |
| `form_goals_for` | bigint | `sum(goals_for) OVER prev5`; null when `form_matches = 0` |
| `form_goals_against` | bigint | `sum(goals_against) OVER prev5`; null when `form_matches = 0` |

`WINDOW prev5 AS (PARTITION BY team_id ORDER BY match_date, match_id ROWS BETWEEN 5 PRECEDING AND
1 PRECEDING)`: strictly earlier matches of the same team, at most 5, with total order `(match_date,
match_id)` (research.md R8). Index (dbt `indexes` config, research.md R14):
`(team_id, match_date, match_id)`.

Generic tests: `unique_combination_of_columns [match_id, team_id]`; `match_id` `not_null` +
`relationships → stg_matches.match_id`; `team_id`, `opponent_team_id` `not_null` +
`relationships → stg_teams.team_id`; `result` `accepted_values [W, D, L]`; `points`
`accepted_values [0, 1, 3]`; `form_matches` `accepted_values [0, 1, 2, 3, 4, 5]`; `not_null` on
`team_match_number`.

### `mart_league_season_home_advantage` — PK `(league_code, season)`

| Column | data_type | Derivation (per `league_code, season, season_start_year` group of `stg_matches`) |
|---|---|---|
| `league_code` | text | |
| `season` | text | |
| `season_start_year` | integer | |
| `matches_played` | bigint | `count(*)` |
| `home_wins`, `draws`, `away_wins` | bigint | `count(*) FILTER (WHERE ft_result = 'H' / 'D' / 'A')` |
| `home_win_share`, `draw_share`, `away_win_share` | numeric(5,4) | `round(<count>::numeric / matches_played, 4)` |
| `avg_home_goals`, `avg_away_goals` | numeric(6,4) | `round(sum(home_goals)::numeric / matches_played, 4)`, same for away |

Generic tests: `unique_combination_of_columns [league_code, season]`; `league_code` `not_null` +
`relationships → stg_leagues.league_code`; `not_null` on every column.

### `mart_league_season_goals` — PK `(league_code, season)`

| Column | data_type | Derivation (same grouping) |
|---|---|---|
| `league_code`, `season`, `season_start_year` | text, text, integer | |
| `matches_played` | bigint | `count(*)` |
| `total_goals` | bigint | `sum(total_goals)` |
| `goals_per_match` | numeric(6,4) | `round(total_goals::numeric / matches_played, 4)` |
| `over_2_5_matches` | bigint | `count(*) FILTER (WHERE total_goals >= 3)` |
| `over_2_5_share` | numeric(5,4) | `round(over_2_5_matches::numeric / matches_played, 4)` |
| `btts_matches` | bigint | `count(*) FILTER (WHERE home_goals > 0 AND away_goals > 0)` |
| `btts_share` | numeric(5,4) | `round(btts_matches::numeric / matches_played, 4)` |

Generic tests: as for home advantage.

### `mart_team_ratings` — PK `(team_id)`; one row per team in `stg_teams` (research.md R9)

| Column | data_type | Derivation |
|---|---|---|
| `team_id` | integer | `stg_teams.team_id` |
| `team_key` | text | |
| `team_name` | text | |
| `rated_matches` | bigint | number of the team's matches with a non-null pre-match Elo for that team; 0 if none |
| `latest_elo`, `latest_elo_date` | double precision, date | rated row first by `match_date DESC, match_id DESC` |
| `peak_elo`, `peak_elo_date` | double precision, date | first by `elo DESC, match_date ASC, match_id ASC` (ties → earliest date) |
| `lowest_elo`, `lowest_elo_date` | double precision, date | first by `elo ASC, match_date ASC, match_id ASC` (ties → earliest date) |

Team perspective: home rows use `home_elo`, away rows use `away_elo`. Rows with a null Elo are left
out of this mart only. `rated_matches = 0` ⇔ all six value/date columns are null.

Generic tests: `team_id` `unique` + `not_null` + `relationships → stg_teams.team_id`; `rated_matches`
`not_null`.

## 4. Singular tests (`transform/tests/*.sql`, research.md R10)

A singular test returns the offending rows (0 rows = pass). Each one has a **mutation case** in
`tests/integration/test_transform_invariants.py::MUTATIONS`. The corruption SQL runs on
`transformed_db` inside a transaction. The test's compiled SQL must then return ≥ 1 row, and the
transaction is rolled back.

| Test | Invariant (independent formulation) | Mutation case (`id`: SQL) |
|---|---|---|
| `assert_team_form_two_rows_per_match` | every `stg_matches.match_id` has exactly one `is_home` and one `NOT is_home` row in `mart_team_form`, with `team_id`/`opponent_team_id` equal to the match's home/away ids; no `mart_team_form.match_id` is missing from staging (`FULL OUTER JOIN` on per-match aggregates) | `team_form_row_missing`: `DELETE FROM analytics_marts.mart_team_form WHERE (match_id, team_id) IN (SELECT match_id, team_id FROM analytics_marts.mart_team_form ORDER BY match_id, team_id LIMIT 1)` |
| `assert_team_form_matches_lag_recomputation` | with `w AS (PARTITION BY team_id ORDER BY match_date, match_id)`: `team_match_number = row_number() OVER w`; `form_matches = least(row_number() OVER w - 1, 5)`; each `form_*` sum `IS NOT DISTINCT FROM` `CASE WHEN row_number() OVER w = 1 THEN NULL ELSE coalesce(lag(x,1) OVER w,0)+…+coalesce(lag(x,5) OVER w,0) END` for `points`, `goals_for`, `goals_against` | `form_includes_current_match`: `UPDATE analytics_marts.mart_team_form SET form_points = form_points + points WHERE form_matches > 0 AND points > 0` |
| `assert_home_advantage_shares_sum_to_one` | `abs(home_win_share + draw_share + away_win_share - 1) > 0.00015` returns rows (research.md R13) | `shares_do_not_sum_to_one`: `UPDATE analytics_marts.mart_league_season_home_advantage SET draw_share = draw_share + 0.01` |
| `assert_home_advantage_reconciles_with_staging` | re-aggregate `stg_matches` per `(league_code, season)`; `FULL OUTER JOIN` the mart; any `IS DISTINCT FROM` on `matches_played`, `home_wins`, `draws`, `away_wins`, and `avg_home_goals` / `avg_away_goals` vs `round(sum(home_goals)::numeric / count(*), 4)` (same for away) recomputed from staging | `home_advantage_count_off`: `UPDATE analytics_marts.mart_league_season_home_advantage SET matches_played = matches_played + 1 WHERE (league_code, season) = (SELECT league_code, season FROM analytics_marts.mart_league_season_home_advantage ORDER BY 1, 2 LIMIT 1)` |
| `assert_goals_reconcile_with_staging` | as above for `matches_played`, `total_goals` (= `sum(home_goals + away_goals)`), `over_2_5_matches`, `btts_matches` | `goals_total_off`: `UPDATE analytics_marts.mart_league_season_goals SET total_goals = total_goals + 1` |
| `assert_team_ratings_reconcile_with_staging` | recompute from `stg_matches` (team perspective, Elo not null) with `DISTINCT ON (team_id) … ORDER BY team_id, <tie order>` for latest/peak/lowest and `count(*)` for `rated_matches`; `LEFT JOIN` from `stg_teams`; any `IS DISTINCT FROM` on the 7 value columns (this also checks that unrated teams are all null) | `peak_date_not_earliest`: `UPDATE analytics_marts.mart_team_ratings SET peak_elo_date = latest_elo_date WHERE team_key = 'arsenal'` (fixture: peak tie 2021-08-14 vs latest 2021-09-18) |

The project-local generic test `tests/generic/unique_combination_of_columns.sql`
(`{% test unique_combination_of_columns(model, combination_of_columns) %}`) selects the column
combination `GROUP BY … HAVING count(*) > 1`.

## 5. Test helpers (Python, `tests/integration/`)

`tests/integration/dbt_runner.py` (new; typed, docstrings, no dbt import):

- `TRANSFORM_DIR: Path` = `REPO_ROOT / "transform"`.
- `@dataclass(frozen=True) class DbtResult`: `returncode: int`, `stdout: str`,
  `results: list[dict[str, object]]` (the `results` of `run_results.json`, or `[]` if the file is
  missing), `target_dir: Path`. Methods: `statuses() -> dict[str, str]` (unique_id → status);
  `compiled_sql(test_name: str) -> str` (the `compiled_code` of the result whose unique_id starts
  with `test.football_analytics.<test_name>.` or equals `test.football_analytics.<test_name>`;
  `KeyError` if absent); `manifest() -> dict[str, object]` (reads `target_dir / "manifest.json"`).
- `dbt_env(database_url: str, home: Path, target_dir: Path) -> dict[str, str]`: parses the libpq URL
  with `urllib.parse.urlsplit` (`unquote` user/password) into `DBT_PG_HOST`, `DBT_PG_PORT`,
  `DBT_PG_USER`, `DBT_ENV_SECRET_PG_PASSWORD`, `DBT_PG_DBNAME`, and adds `PATH` (from `os.environ`),
  `HOME=str(home)`, `DBT_TARGET_PATH=str(target_dir / "target")`,
  `DBT_LOG_PATH=str(target_dir / "logs")`. Nothing else from `os.environ` is passed.
- `run_dbt(args: Sequence[str], env: Mapping[str, str], *, project_dir: Path = TRANSFORM_DIR,
  timeout: float = 300) -> DbtResult`: runs
  `[str(Path(sys.executable).with_name("dbt")), *args, "--project-dir", str(project_dir),
  "--profiles-dir", str(project_dir)]` with `subprocess.run(..., env=dict(env), capture_output=True,
  text=True, timeout=timeout, check=False)`. It returns `DbtResult` with stdout + stderr
  concatenated, and logs the command (without the password) at DEBUG.

`tests/integration/helpers.py` (extended):

- `ANALYTICS_MODELS: dict[str, str]`: model → `"<schema>.<name>"` for the 7 models.
- `ANALYTICS_KEYS: dict[str, str]`: model → `ORDER BY` clause (§2, §3 keys).
- `analytics_snapshot(conn) -> dict[str, list[object]]`: for each model,
  `SELECT to_jsonb(t) FROM <schema>.<name> t ORDER BY <key>`.
- `analytics_catalog_snapshot(conn) -> dict[str, list[tuple[object, ...]]]`: like
  `catalog_snapshot` but for schemas `analytics_staging`/`analytics_marts`, with table types from
  `information_schema.tables`. Index rows are `(tablename, regexp_replace(indexdef, 'INDEX \S+ ON',
  'INDEX ON'))`, constraints are `(relname, contype, pg_get_constraintdef(oid))` (names left out,
  research.md R12), and the list is sorted.
- `mart_rows(conn, mart: str) -> list[dict[str, object]]`: mart rows with ids replaced by natural
  keys (`match_id` → `source_row_number` via `matches`, `team_id`/`opponent_team_id` → `team_key`
  via `teams`). Numerics become `str` (`numeric::text`), dates ISO strings, Elo floats. Ordering
  matches the expected JSON (plan.md "Test Fixtures").

`tests/integration/conftest.py` (extended): session fixture
`transformed_db(admin_url, template_db, tmp_path_factory) -> Iterator[TransformedDb]`.
`TransformedDb` is a frozen dataclass with `url`, `build: DbtResult`, `public_before`,
`public_after` (each `(snapshot, catalog_snapshot)`). It clones the template, ingests
`matches_transform.csv` with `pipeline.run(Settings(...), source_file=..., started_at=
CONTRACT_STARTED_AT)`, captures `public_before`, runs `dbt build`, captures `public_after`, yields,
and drops the database. The fixture does **not** assert success. Tests do, so a failed build shows
up as named test failures and not as an error in setup.

## 6. Measurement script (`scripts/explain_transforms.sql`, research.md R14)

A psql script (`psql -v ON_ERROR_STOP=1 -f`), read-only in effect. For each query below it runs
`EXPLAIN (ANALYZE, BUFFERS)` 5 times, then reports the median from the 5 plans' "Execution Time".
The median is computed by the operator from the output; the script only prints the plans. "Before"
plans for dbt-created indexes and keys run inside `BEGIN; DROP INDEX …` or
`ALTER TABLE … DROP CONSTRAINT …; … ROLLBACK;`, with the names looked up at run time by
`\gset` from `pg_indexes`/`pg_constraint`. Candidates on `public` run inside `BEGIN; CREATE INDEX …;
ANALYZE matches; … ROLLBACK;`.

1. Team form lookup: `SELECT * FROM analytics_marts.mart_team_form WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal') ORDER BY match_date DESC, match_id DESC LIMIT 10`, before (index dropped) and after.
2. Staging/mart source scan: the grouping query of `mart_league_season_goals` written out against the view (`SELECT league_code, season, count(*), sum(total_goals) FROM analytics_staging.stg_matches GROUP BY 1, 2`), without and with candidate `matches (match_date)`.
3. Cross-league date range: `SELECT count(*) FROM matches WHERE match_date BETWEEN '2021-08-14' AND '2021-08-15'`, without and with candidate `matches (match_date)`.
4. League + date range: `SELECT count(*) FROM matches WHERE league_code = 'E0' AND match_date BETWEEN '2015-07-01' AND '2016-06-30'` (existing natural key; candidate `(league_code, match_date)` is shown redundant).
5. League-season lookup: `SELECT * FROM analytics_marts.mart_league_season_home_advantage WHERE league_code = 'E0' AND season = '2015/16'`, with and without the primary key.
6. Team rating lookup: `SELECT * FROM analytics_marts.mart_team_ratings WHERE team_id = …`, with and without the primary key.

## 7. Validation rules summary (→ tests)

| Rule | Where enforced |
|---|---|
| Season label rule (FR-003) | `stg_matches` derivation; `test_season_label_rule`; fixture expectations |
| Result/points per side (FR-002) | `stg_matches`; `accepted_values`; `test_staging_derivations_match_expected` |
| Form uses only strictly earlier matches, at most 5 (FR-005) | window `prev5`; `assert_team_form_matches_lag_recomputation`; `test_team_form_matches_expected` |
| Two team-match rows per match | `assert_team_form_two_rows_per_match`; PK |
| Shares sum to 1 within 0.00015 | `assert_home_advantage_shares_sum_to_one`; `test_shares_sum_to_one_within_rounding` |
| Totals reconcile with staging | `assert_home_advantage_reconciles_with_staging`, `assert_goals_reconcile_with_staging` |
| Ratings tie rules, unrated ⇒ null | `assert_team_ratings_reconcile_with_staging`; `test_team_ratings_match_expected` |
| Keys unique / not null / relationships (FR-006) | generic tests + PKs; `test_every_model_has_key_and_relationship_tests` |
| Mart column types | model contracts; `test_mart_columns_match_contract` |
