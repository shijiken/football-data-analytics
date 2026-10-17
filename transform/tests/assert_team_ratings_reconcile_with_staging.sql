-- Recomputes every rating with DISTINCT ON (instead of the model's row_number windows) from the
-- team perspective of stg_matches, left-joined from stg_teams. Any difference fails, which also
-- proves that a team without Elo has only nulls.
with sides as (

    select home_team_id as team_id, match_id, match_date, home_elo as elo
    from {{ ref('stg_matches') }}
    where home_elo is not null

    union all

    select away_team_id as team_id, match_id, match_date, away_elo as elo
    from {{ ref('stg_matches') }}
    where away_elo is not null

),

latest as (

    select distinct on (team_id) team_id, elo, match_date
    from sides
    order by team_id, match_date desc, match_id desc

),

peak as (

    select distinct on (team_id) team_id, elo, match_date
    from sides
    order by team_id, elo desc, match_date asc, match_id asc

),

lowest as (

    select distinct on (team_id) team_id, elo, match_date
    from sides
    order by team_id, elo asc, match_date asc, match_id asc

),

counts as (

    select team_id, count(*) as n
    from sides
    group by team_id

),

expected as (

    select
        teams.team_id,
        teams.team_key,
        teams.team_name,
        coalesce(counts.n, 0) as rated_matches,
        latest.elo as latest_elo,
        latest.match_date as latest_elo_date,
        peak.elo as peak_elo,
        peak.match_date as peak_elo_date,
        lowest.elo as lowest_elo,
        lowest.match_date as lowest_elo_date
    from {{ ref('stg_teams') }} as teams
    left join counts on counts.team_id = teams.team_id
    left join latest on latest.team_id = teams.team_id
    left join peak on peak.team_id = teams.team_id
    left join lowest on lowest.team_id = teams.team_id

)

select coalesce(expected.team_id, mart.team_id) as team_id
from expected
full outer join {{ ref('mart_team_ratings') }} as mart on mart.team_id = expected.team_id
where expected.team_id is null
    or mart.team_id is null
    or mart.team_key is distinct from expected.team_key
    or mart.team_name is distinct from expected.team_name
    or mart.rated_matches is distinct from expected.rated_matches
    or mart.latest_elo is distinct from expected.latest_elo
    or mart.latest_elo_date is distinct from expected.latest_elo_date
    or mart.peak_elo is distinct from expected.peak_elo
    or mart.peak_elo_date is distinct from expected.peak_elo_date
    or mart.lowest_elo is distinct from expected.lowest_elo
    or mart.lowest_elo_date is distinct from expected.lowest_elo_date
