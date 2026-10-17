-- One row per loaded match with the derived fields every mart needs: season label (July
-- cut-over, FR-003), per-side result and points (FR-002) and total goals.
with matches as (

    select
        match_id,
        league_code,
        match_date,
        home_team_id,
        away_team_id,
        ft_home_goals::integer as home_goals,
        ft_away_goals::integer as away_goals,
        ft_result,
        home_elo,
        away_elo
    from {{ source('football', 'matches') }}

),

seasons as (

    select
        *,
        case
            when extract(month from match_date) >= 7
                then extract(year from match_date)::integer
            else extract(year from match_date)::integer - 1
        end as season_start_year
    from matches

),

results as (

    select
        *,
        case ft_result when 'H' then 'W' when 'D' then 'D' else 'L' end as home_result,
        case ft_result when 'H' then 'L' when 'D' then 'D' else 'W' end as away_result
    from seasons

)

select
    match_id,
    league_code,
    match_date,
    season_start_year,
    season_start_year::text || '/' || lpad(((season_start_year + 1) % 100)::text, 2, '0')
        as season,
    home_team_id,
    away_team_id,
    home_goals,
    away_goals,
    home_goals + away_goals as total_goals,
    ft_result,
    home_result,
    away_result,
    case home_result when 'W' then 3 when 'D' then 1 else 0 end as home_points,
    case away_result when 'W' then 3 when 'D' then 1 else 0 end as away_points,
    home_elo,
    away_elo
from results
