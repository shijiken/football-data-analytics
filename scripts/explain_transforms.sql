-- Measurement procedure for the analytics transforms (specs/003-dbt-transforms, FR-010, SC-005).
--
-- Usage (read-only in effect; see the notes below):
--   psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f scripts/explain_transforms.sql
--   docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
--     -f - < scripts/explain_transforms.sql
--
-- Every query is explained 5 times per variant with EXPLAIN (ANALYZE, BUFFERS); the operator takes
-- the median "Execution Time" from the output. The "before" and candidate variants change the
-- schema only inside BEGIN ... ROLLBACK, with index and constraint names looked up at run time, so
-- nothing persists. No planner setting is overridden. Do not run it while an ingest is running:
-- the candidate index on public.matches holds a lock that blocks writes until the rollback.
--
-- Variants (11, so 55 "Execution Time:" lines):
--   Q1 team form lookup on mart_team_form      (2): with the dbt index / index dropped
--   Q2 staging scan grouped by league-season    (2): without / with candidate matches (match_date)
--   Q3 cross-league date range on matches       (2): without / with candidate matches (match_date)
--   Q4 league + date range on matches           (1): existing natural key (candidate
--                                                    (league_code, match_date) is redundant)
--   Q5 league-season lookup on home advantage   (2): with the primary key / key dropped
--   Q6 team rating lookup on mart_team_ratings  (2): with the primary key / key dropped

\pset pager off
\timing off

\echo
\echo Q1: team form lookup, with the dbt index (after)
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_form
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal')
ORDER BY match_date DESC, match_id DESC
LIMIT 10;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_form
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal')
ORDER BY match_date DESC, match_id DESC
LIMIT 10;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_form
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal')
ORDER BY match_date DESC, match_id DESC
LIMIT 10;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_form
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal')
ORDER BY match_date DESC, match_id DESC
LIMIT 10;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_form
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal')
ORDER BY match_date DESC, match_id DESC
LIMIT 10;

\echo Q1: team form lookup, index dropped (before)
BEGIN;
SELECT indexname AS idx FROM pg_indexes
WHERE schemaname = 'analytics_marts' AND tablename = 'mart_team_form'
  AND indexdef LIKE '%USING btree (team_id, match_date, match_id)' \gset
DROP INDEX analytics_marts.:"idx";

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_form
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal')
ORDER BY match_date DESC, match_id DESC
LIMIT 10;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_form
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal')
ORDER BY match_date DESC, match_id DESC
LIMIT 10;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_form
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal')
ORDER BY match_date DESC, match_id DESC
LIMIT 10;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_form
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal')
ORDER BY match_date DESC, match_id DESC
LIMIT 10;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_form
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal')
ORDER BY match_date DESC, match_id DESC
LIMIT 10;

ROLLBACK;

\echo Q2: staging scan grouped by league-season, no candidate index (before)
EXPLAIN (ANALYZE, BUFFERS)
SELECT league_code, season, count(*), sum(total_goals)
FROM analytics_staging.stg_matches
GROUP BY 1, 2;

EXPLAIN (ANALYZE, BUFFERS)
SELECT league_code, season, count(*), sum(total_goals)
FROM analytics_staging.stg_matches
GROUP BY 1, 2;

EXPLAIN (ANALYZE, BUFFERS)
SELECT league_code, season, count(*), sum(total_goals)
FROM analytics_staging.stg_matches
GROUP BY 1, 2;

EXPLAIN (ANALYZE, BUFFERS)
SELECT league_code, season, count(*), sum(total_goals)
FROM analytics_staging.stg_matches
GROUP BY 1, 2;

EXPLAIN (ANALYZE, BUFFERS)
SELECT league_code, season, count(*), sum(total_goals)
FROM analytics_staging.stg_matches
GROUP BY 1, 2;

\echo Q2: staging scan grouped by league-season, candidate matches (match_date) (after)
BEGIN;
CREATE INDEX matches_match_date_candidate ON public.matches (match_date);
ANALYZE public.matches;

EXPLAIN (ANALYZE, BUFFERS)
SELECT league_code, season, count(*), sum(total_goals)
FROM analytics_staging.stg_matches
GROUP BY 1, 2;

EXPLAIN (ANALYZE, BUFFERS)
SELECT league_code, season, count(*), sum(total_goals)
FROM analytics_staging.stg_matches
GROUP BY 1, 2;

EXPLAIN (ANALYZE, BUFFERS)
SELECT league_code, season, count(*), sum(total_goals)
FROM analytics_staging.stg_matches
GROUP BY 1, 2;

EXPLAIN (ANALYZE, BUFFERS)
SELECT league_code, season, count(*), sum(total_goals)
FROM analytics_staging.stg_matches
GROUP BY 1, 2;

EXPLAIN (ANALYZE, BUFFERS)
SELECT league_code, season, count(*), sum(total_goals)
FROM analytics_staging.stg_matches
GROUP BY 1, 2;

ROLLBACK;

\echo Q3: cross-league date range, no candidate index (before)
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.matches
WHERE match_date BETWEEN '2021-08-14' AND '2021-08-15';

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.matches
WHERE match_date BETWEEN '2021-08-14' AND '2021-08-15';

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.matches
WHERE match_date BETWEEN '2021-08-14' AND '2021-08-15';

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.matches
WHERE match_date BETWEEN '2021-08-14' AND '2021-08-15';

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.matches
WHERE match_date BETWEEN '2021-08-14' AND '2021-08-15';

\echo Q3: cross-league date range, candidate matches (match_date) (after)
BEGIN;
CREATE INDEX matches_match_date_candidate ON public.matches (match_date);
ANALYZE public.matches;

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.matches
WHERE match_date BETWEEN '2021-08-14' AND '2021-08-15';

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.matches
WHERE match_date BETWEEN '2021-08-14' AND '2021-08-15';

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.matches
WHERE match_date BETWEEN '2021-08-14' AND '2021-08-15';

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.matches
WHERE match_date BETWEEN '2021-08-14' AND '2021-08-15';

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.matches
WHERE match_date BETWEEN '2021-08-14' AND '2021-08-15';

ROLLBACK;

\echo Q4: league + date range, existing natural key
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.matches
WHERE league_code = 'E0' AND match_date BETWEEN '2015-07-01' AND '2016-06-30';

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.matches
WHERE league_code = 'E0' AND match_date BETWEEN '2015-07-01' AND '2016-06-30';

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.matches
WHERE league_code = 'E0' AND match_date BETWEEN '2015-07-01' AND '2016-06-30';

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.matches
WHERE league_code = 'E0' AND match_date BETWEEN '2015-07-01' AND '2016-06-30';

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.matches
WHERE league_code = 'E0' AND match_date BETWEEN '2015-07-01' AND '2016-06-30';

\echo Q5: league-season lookup, with the primary key (after)
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_league_season_home_advantage
WHERE league_code = 'E0' AND season = '2015/16';

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_league_season_home_advantage
WHERE league_code = 'E0' AND season = '2015/16';

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_league_season_home_advantage
WHERE league_code = 'E0' AND season = '2015/16';

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_league_season_home_advantage
WHERE league_code = 'E0' AND season = '2015/16';

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_league_season_home_advantage
WHERE league_code = 'E0' AND season = '2015/16';

\echo Q5: league-season lookup, primary key dropped (before)
BEGIN;
SELECT conname AS pk FROM pg_constraint
WHERE conrelid = 'analytics_marts.mart_league_season_home_advantage'::regclass AND contype = 'p' \gset
ALTER TABLE analytics_marts.mart_league_season_home_advantage DROP CONSTRAINT :"pk";

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_league_season_home_advantage
WHERE league_code = 'E0' AND season = '2015/16';

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_league_season_home_advantage
WHERE league_code = 'E0' AND season = '2015/16';

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_league_season_home_advantage
WHERE league_code = 'E0' AND season = '2015/16';

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_league_season_home_advantage
WHERE league_code = 'E0' AND season = '2015/16';

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_league_season_home_advantage
WHERE league_code = 'E0' AND season = '2015/16';

ROLLBACK;

\echo Q6: team rating lookup, with the primary key (after)
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_ratings
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal');

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_ratings
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal');

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_ratings
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal');

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_ratings
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal');

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_ratings
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal');

\echo Q6: team rating lookup, primary key dropped (before)
BEGIN;
SELECT conname AS pk FROM pg_constraint
WHERE conrelid = 'analytics_marts.mart_team_ratings'::regclass AND contype = 'p' \gset
ALTER TABLE analytics_marts.mart_team_ratings DROP CONSTRAINT :"pk";

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_ratings
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal');

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_ratings
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal');

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_ratings
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal');

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_ratings
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal');

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM analytics_marts.mart_team_ratings
WHERE team_id = (SELECT team_id FROM analytics_staging.stg_teams WHERE team_key = 'arsenal');

ROLLBACK;
