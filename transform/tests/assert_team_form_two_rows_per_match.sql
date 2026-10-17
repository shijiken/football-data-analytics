-- Every staged match has exactly one home-side and one away-side row in mart_team_form, carrying
-- that side's ids and values, and no mart match is missing from staging. Returns the offending matches.
with staged as (

    select * from {{ ref('stg_matches') }}

),

mart as (

    select * from {{ ref('mart_team_form') }}

),

home_side as (

    select
        match_id,
        count(*) as n,
        max(team_id) as team_id,
        max(opponent_team_id) as opponent_team_id,
        max(league_code) as league_code,
        max(season) as season,
        max(match_date) as match_date,
        max(goals_for) as goals_for,
        max(goals_against) as goals_against,
        max(result) as result,
        max(points) as points
    from mart
    where is_home
    group by match_id

),

away_side as (

    select
        match_id,
        count(*) as n,
        max(team_id) as team_id,
        max(opponent_team_id) as opponent_team_id,
        max(league_code) as league_code,
        max(season) as season,
        max(match_date) as match_date,
        max(goals_for) as goals_for,
        max(goals_against) as goals_against,
        max(result) as result,
        max(points) as points
    from mart
    where not is_home
    group by match_id

),

all_matches as (

    select match_id from staged
    union
    select match_id from mart

)

select all_matches.match_id
from all_matches
left join staged on staged.match_id = all_matches.match_id
left join home_side on home_side.match_id = all_matches.match_id
left join away_side on away_side.match_id = all_matches.match_id
where staged.match_id is null
    or home_side.match_id is null
    or away_side.match_id is null
    or home_side.n <> 1
    or away_side.n <> 1
    or home_side.team_id is distinct from staged.home_team_id
    or home_side.opponent_team_id is distinct from staged.away_team_id
    or home_side.goals_for is distinct from staged.home_goals
    or home_side.goals_against is distinct from staged.away_goals
    or home_side.result is distinct from staged.home_result
    or home_side.points is distinct from staged.home_points
    or away_side.team_id is distinct from staged.away_team_id
    or away_side.opponent_team_id is distinct from staged.home_team_id
    or away_side.goals_for is distinct from staged.away_goals
    or away_side.goals_against is distinct from staged.home_goals
    or away_side.result is distinct from staged.away_result
    or away_side.points is distinct from staged.away_points
    or home_side.league_code is distinct from staged.league_code
    or away_side.league_code is distinct from staged.league_code
    or home_side.season is distinct from staged.season
    or away_side.season is distinct from staged.season
    or home_side.match_date is distinct from staged.match_date
    or away_side.match_date is distinct from staged.match_date
