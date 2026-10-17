-- Team-match grain (two rows per loaded match) plus the form over the team's previous five
-- matches. The frame ends one row before the current row, so the current match is never
-- included, and the (match_date, match_id) order is total (research.md R8).

with matches as (

    select * from {{ ref('stg_matches') }}

),

team_matches as (

    select
        match_id,
        home_team_id as team_id,
        away_team_id as opponent_team_id,
        league_code,
        season,
        season_start_year,
        match_date,
        true as is_home,
        home_goals as goals_for,
        away_goals as goals_against,
        home_result as result,
        home_points as points
    from matches

    union all

    select
        match_id,
        away_team_id as team_id,
        home_team_id as opponent_team_id,
        league_code,
        season,
        season_start_year,
        match_date,
        false as is_home,
        away_goals as goals_for,
        home_goals as goals_against,
        away_result as result,
        away_points as points
    from matches

)

select
    match_id,
    team_id,
    opponent_team_id,
    league_code,
    season,
    season_start_year,
    match_date,
    is_home,
    goals_for,
    goals_against,
    result,
    points,
    row_number() over (partition by team_id order by match_date, match_id) as team_match_number,
    count(*) over prev5 as form_matches,
    sum(points) over prev5 as form_points,
    sum(goals_for) over prev5 as form_goals_for,
    sum(goals_against) over prev5 as form_goals_against
from team_matches
window prev5 as (
    partition by team_id
    order by match_date, match_id
    rows between 5 preceding and 1 preceding
)
