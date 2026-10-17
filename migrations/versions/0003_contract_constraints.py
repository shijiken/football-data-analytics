"""Data contract constraints: nine named CHECKs on matches, added NOT VALID.

Revision ID: 0003_contract_constraints
Revises: 0002_contract_run_metadata

NOT VALID enforces the rules for every new or updated row without scanning existing rows, so the
migration succeeds on a database that already holds violating matches (FR-008). The constraints
are never validated by the application (research.md R8). A NULL input makes a comparison NULL,
which a CHECK accepts: the rule is skipped, exactly as in the pipeline.
"""

from alembic import op

revision = "0003_contract_constraints"
down_revision = "0002_contract_run_metadata"
branch_labels = None
depends_on = None

_FT_RESULT = (
    "ft_result = CASE WHEN ft_home_goals > ft_away_goals THEN 'H' "
    "WHEN ft_home_goals = ft_away_goals THEN 'D' "
    "WHEN ft_home_goals < ft_away_goals THEN 'A' END"
)
_HT_RESULT = (
    "ht_result = CASE WHEN ht_home_goals > ht_away_goals THEN 'H' "
    "WHEN ht_home_goals = ht_away_goals THEN 'D' "
    "WHEN ht_home_goals < ht_away_goals THEN 'A' END"
)

#: (rule id, CHECK expression) in contract order; names are ``matches_contract_<rule id>``.
CONSTRAINTS: tuple[tuple[str, str], ...] = (
    ("ht_le_ft_home", "ht_home_goals <= ft_home_goals"),
    ("ht_le_ft_away", "ht_away_goals <= ft_away_goals"),
    ("ft_result_consistent", _FT_RESULT),
    ("ht_result_consistent", _HT_RESULT),
    ("sot_le_shots_home", "home_shots_on_target <= home_shots"),
    ("sot_le_shots_away", "away_shots_on_target <= away_shots"),
    (
        "odds_min",
        "odd_home >= 1.0 AND odd_draw >= 1.0 AND odd_away >= 1.0 AND max_odd_home >= 1.0 "
        "AND max_odd_draw >= 1.0 AND max_odd_away >= 1.0",
    ),
    (
        "max_odds_ge_avg",
        "max_odd_home >= odd_home AND max_odd_draw >= odd_draw AND max_odd_away >= odd_away",
    ),
    (
        "date_in_range",
        "match_date >= DATE '2000-01-01' AND match_date <= (now() AT TIME ZONE 'UTC')::date",
    ),
)


def upgrade() -> None:
    for rule_id, expression in CONSTRAINTS:
        op.execute(
            f"ALTER TABLE matches ADD CONSTRAINT matches_contract_{rule_id} "
            f"CHECK ({expression}) NOT VALID"
        )


def downgrade() -> None:
    for rule_id, _expression in reversed(CONSTRAINTS):
        op.execute(f"ALTER TABLE matches DROP CONSTRAINT matches_contract_{rule_id}")
