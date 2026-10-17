-- Re-aggregates stg_matches per league-season and compares counts, totals and ratios with
-- mart_league_season_goals. A missing or extra league-season also fails.
with staged as (

    select
        league_code,
        season,
        max(season_start_year) as season_start_year,
        count(*) as n,
        sum(home_goals + away_goals) as goals,
        count(*) filter (where home_goals + away_goals > 2) as over,
        count(*) filter (where home_goals > 0 and away_goals > 0) as btts
    from {{ ref('stg_matches') }}
    group by league_code, season

),

mart as (

    select * from {{ ref('mart_league_season_goals') }}

)

select coalesce(staged.league_code, mart.league_code) as league_code,
    coalesce(staged.season, mart.season) as season
from staged
full outer join mart
    on mart.league_code = staged.league_code and mart.season = staged.season
where staged.league_code is null
    or mart.league_code is null
    or mart.season_start_year is distinct from staged.season_start_year
    or mart.matches_played is distinct from staged.n
    or mart.total_goals is distinct from staged.goals
    or mart.over_2_5_matches is distinct from staged.over
    or mart.btts_matches is distinct from staged.btts
    or mart.goals_per_match is distinct from round(staged.goals::numeric / staged.n, 4)
    or mart.over_2_5_share is distinct from round(staged.over::numeric / staged.n, 4)
    or mart.btts_share is distinct from round(staged.btts::numeric / staged.n, 4)
