select league_code
from {{ source('football', 'leagues') }}
