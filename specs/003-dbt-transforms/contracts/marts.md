# Contract: analytics marts (consumer interface)

**Feature**: `003-dbt-transforms` | Exact columns and types: [data-model.md](../data-model.md) §3
(enforced by dbt model contracts; gated by `test_mart_columns_match_contract`)

This is the interface the Phase 5 API and the dashboard will read. Columns are only added within
this feature. A rename, a removal or a type change is a breaking change and needs a spec.

## Conventions

- Schema `analytics_marts`, tables rebuilt in full by every `dbt build` (atomic swap per table).
- **Season** (FR-003): matches dated July–December belong to season `YYYY/YY+1` (start year
  `YYYY`), January–June to `YYYY-1/YY`. The same rule applies to every league, including
  calendar-year leagues (ARG, BRA, USA, …), whose calendar seasons therefore span two labels.
  `season_start_year` is the sortable form.
- **Order of a team's matches**: `match_date`, then `match_id`. `match_id` is the ingestion
  surrogate key; it is stable across re-ingests and follows source-file order on a fresh load.
- **Shares** are `numeric(5,4)` in [0, 1], rounded half away from zero; the three home-advantage
  shares sum to 1 within 0.00015 (rounding). Raw counts are always present for exact arithmetic.
- **Averages** are `numeric(6,4)`.
- Only loaded matches count (rows excluded by ingestion never appear).

## Tables

| Table | Grain / key | Answers |
|---|---|---|
| `mart_team_form` | one row per team per match, PK `(match_id, team_id)`; indexed `(team_id, match_date, match_id)` | a team's result, points and goals in each match, and its form over its previous ≤ 5 matches (`form_matches` 0–5; `form_points`, `form_goals_for`, `form_goals_against` null when `form_matches = 0`) |
| `mart_league_season_home_advantage` | one row per league-season, PK `(league_code, season)` | matches played, home wins / draws / away wins with shares, average home and away goals |
| `mart_league_season_goals` | one row per league-season, PK `(league_code, season)` | total goals, goals per match, matches and share with over 2.5 goals (≥ 3) and with both teams scoring |
| `mart_team_ratings` | one row per team, PK `(team_id)` | number of rated matches; latest, peak and lowest pre-match Elo from the source, with dates. Peak/lowest ties → earliest date (then lowest `match_id`); latest = newest rated match. Teams with no Elo: `rated_matches = 0`, all values null |

Typical queries (served by the indexes measured in the README):

```sql
SELECT * FROM analytics_marts.mart_team_form
WHERE team_id = $1 ORDER BY match_date DESC, match_id DESC LIMIT 10;

SELECT * FROM analytics_marts.mart_league_season_home_advantage
WHERE league_code = $1 ORDER BY season_start_year;
```

Staging views (`analytics_staging.stg_leagues`, `stg_teams`, `stg_matches`) are internal building
blocks. Consumers should read marts.
