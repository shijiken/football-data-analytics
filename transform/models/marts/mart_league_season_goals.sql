-- One row per league-season: total goals, goals per match, and the over-2.5 and both-teams-score
-- counts and shares.
with matches as (

    select * from {{ ref('stg_matches') }}

),

grouped as (

    select
        league_code,
        season,
        season_start_year,
        count(*) as matches_played,
        sum(total_goals) as total_goals,
        count(*) filter (where total_goals >= 3) as over_2_5_matches,
        count(*) filter (where home_goals > 0 and away_goals > 0) as btts_matches
    from matches
    group by league_code, season, season_start_year

)

select
    league_code,
    season,
    season_start_year,
    matches_played,
    total_goals,
    round(total_goals::numeric / matches_played, 4)::numeric(6, 4) as goals_per_match,
    over_2_5_matches,
    round(over_2_5_matches::numeric / matches_played, 4)::numeric(5, 4) as over_2_5_share,
    btts_matches,
    round(btts_matches::numeric / matches_played, 4)::numeric(5, 4) as btts_share
from grouped
