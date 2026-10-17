-- One row per league-season: outcome counts and shares, and average goals per side.
with matches as (

    select * from {{ ref('stg_matches') }}

),

grouped as (

    select
        league_code,
        season,
        season_start_year,
        count(*) as matches_played,
        count(*) filter (where ft_result = 'H') as home_wins,
        count(*) filter (where ft_result = 'D') as draws,
        count(*) filter (where ft_result = 'A') as away_wins,
        sum(home_goals) as home_goals_total,
        sum(away_goals) as away_goals_total
    from matches
    group by league_code, season, season_start_year

)

select
    league_code,
    season,
    season_start_year,
    matches_played,
    home_wins,
    draws,
    away_wins,
    round(home_wins::numeric / matches_played, 4)::numeric(5, 4) as home_win_share,
    round(draws::numeric / matches_played, 4)::numeric(5, 4) as draw_share,
    round(away_wins::numeric / matches_played, 4)::numeric(5, 4) as away_win_share,
    round(home_goals_total::numeric / matches_played, 4)::numeric(6, 4) as avg_home_goals,
    round(away_goals_total::numeric / matches_played, 4)::numeric(6, 4) as avg_away_goals
from grouped
