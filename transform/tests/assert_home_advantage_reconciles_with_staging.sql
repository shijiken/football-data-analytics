-- Re-aggregates stg_matches per league-season and compares every count, share and average with
-- mart_league_season_home_advantage. A missing or extra league-season also fails.
with staged as (

    select
        league_code,
        season,
        max(season_start_year) as season_start_year,
        count(*) as n,
        count(*) filter (where ft_result = 'H') as h,
        count(*) filter (where ft_result = 'D') as d,
        count(*) filter (where ft_result = 'A') as a,
        sum(home_goals) as home_goals,
        sum(away_goals) as away_goals
    from {{ ref('stg_matches') }}
    group by league_code, season

),

mart as (

    select * from {{ ref('mart_league_season_home_advantage') }}

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
    or mart.home_wins is distinct from staged.h
    or mart.draws is distinct from staged.d
    or mart.away_wins is distinct from staged.a
    or mart.home_win_share is distinct from round(staged.h::numeric / staged.n, 4)
    or mart.draw_share is distinct from round(staged.d::numeric / staged.n, 4)
    or mart.away_win_share is distinct from round(staged.a::numeric / staged.n, 4)
    or mart.avg_home_goals is distinct from round(staged.home_goals::numeric / staged.n, 4)
    or mart.avg_away_goals is distinct from round(staged.away_goals::numeric / staged.n, 4)
