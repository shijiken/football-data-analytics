-- The three rounded shares sum to 1 within 0.00015, the largest rounding error of three values
-- rounded to four places (research.md R13). Returns the league-seasons outside the tolerance.
select
    league_code,
    season,
    home_win_share + draw_share + away_win_share as share_sum
from {{ ref('mart_league_season_home_advantage') }}
where abs(home_win_share + draw_share + away_win_share - 1) > 0.00015
