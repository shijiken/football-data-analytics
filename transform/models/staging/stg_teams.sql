select
    team_id,
    team_key,
    name as team_name
from {{ source('football', 'teams') }}
