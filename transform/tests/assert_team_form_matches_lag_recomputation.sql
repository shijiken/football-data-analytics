-- Recomputes the team number and the previous-five form with LAG offsets 1 to 5 instead of a
-- window frame: a frame that included the current or a later row would disagree. Returns the
-- mart rows whose numbers differ.
with recomputed as (

    select
        match_id,
        team_id,
        team_match_number,
        form_matches,
        form_points,
        form_goals_for,
        form_goals_against,
        row_number() over w as rn,
        coalesce(lag(points, 1) over w, 0) + coalesce(lag(points, 2) over w, 0)
            + coalesce(lag(points, 3) over w, 0) + coalesce(lag(points, 4) over w, 0)
            + coalesce(lag(points, 5) over w, 0) as points_sum,
        coalesce(lag(goals_for, 1) over w, 0) + coalesce(lag(goals_for, 2) over w, 0)
            + coalesce(lag(goals_for, 3) over w, 0) + coalesce(lag(goals_for, 4) over w, 0)
            + coalesce(lag(goals_for, 5) over w, 0) as goals_for_sum,
        coalesce(lag(goals_against, 1) over w, 0) + coalesce(lag(goals_against, 2) over w, 0)
            + coalesce(lag(goals_against, 3) over w, 0) + coalesce(lag(goals_against, 4) over w, 0)
            + coalesce(lag(goals_against, 5) over w, 0) as goals_against_sum
    from {{ ref('mart_team_form') }}
    window w as (partition by team_id order by match_date, match_id)

)

select match_id, team_id
from recomputed
where team_match_number is distinct from rn
    or form_matches is distinct from least(rn - 1, 5)
    or form_points is distinct from (case when rn = 1 then null else points_sum end)
    or form_goals_for is distinct from (case when rn = 1 then null else goals_for_sum end)
    or form_goals_against is distinct from (case when rn = 1 then null else goals_against_sum end)
