-- One row per team: latest, peak and lowest pre-match Elo with dates. Ties for peak and lowest
-- go to the earliest date, then the lowest match_id (research.md R9). Matches without an Elo for
-- the team are left out of this mart only; teams with none keep a row of nulls.
with matches as (

    select * from {{ ref('stg_matches') }}

),

team_elos as (

    select home_team_id as team_id, match_id, match_date, home_elo as elo
    from matches
    where home_elo is not null

    union all

    select away_team_id as team_id, match_id, match_date, away_elo as elo
    from matches
    where away_elo is not null

),

ranked as (

    select
        team_id,
        match_date,
        elo,
        row_number() over (
            partition by team_id order by match_date desc, match_id desc
        ) as latest_rank,
        row_number() over (
            partition by team_id order by elo desc, match_date asc, match_id asc
        ) as peak_rank,
        row_number() over (
            partition by team_id order by elo asc, match_date asc, match_id asc
        ) as lowest_rank
    from team_elos

),

rated as (

    select
        team_id,
        count(*) as rated_matches,
        max(elo) filter (where latest_rank = 1) as latest_elo,
        max(match_date) filter (where latest_rank = 1) as latest_elo_date,
        max(elo) filter (where peak_rank = 1) as peak_elo,
        max(match_date) filter (where peak_rank = 1) as peak_elo_date,
        max(elo) filter (where lowest_rank = 1) as lowest_elo,
        max(match_date) filter (where lowest_rank = 1) as lowest_elo_date
    from ranked
    group by team_id

)

select
    teams.team_id,
    teams.team_key,
    teams.team_name,
    coalesce(rated.rated_matches, 0) as rated_matches,
    rated.latest_elo,
    rated.latest_elo_date,
    rated.peak_elo,
    rated.peak_elo_date,
    rated.lowest_elo,
    rated.lowest_elo_date
from {{ ref('stg_teams') }} as teams
left join rated on rated.team_id = teams.team_id
