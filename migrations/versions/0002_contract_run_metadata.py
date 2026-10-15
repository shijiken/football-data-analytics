"""Data contract run metadata: four ingest_runs columns and the contract_violation reason.

Revision ID: 0002_contract_run_metadata
Revises: 0001_initial_schema

Existing runs are back-filled as contract version 0 (feature 001 rules only). The defaults are
dropped afterwards so the application must always write all four columns (data-model.md 1.2).
"""

from alembic import op

revision = "0002_contract_run_metadata"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None

_OLD_REASONS = (
    "'missing_field', 'invalid_date', 'missing_result', 'invalid_value', 'same_team', 'duplicate'"
)
_NEW_REASONS = (
    "'missing_field', 'invalid_date', 'missing_result', 'invalid_value', 'same_team', "
    "'contract_violation', 'duplicate'"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ingest_runs "
        "ADD COLUMN contract_version integer NOT NULL DEFAULT 0 CHECK (contract_version >= 0), "
        "ADD COLUMN violations_by_rule jsonb NOT NULL DEFAULT '{}'::jsonb "
        "CHECK (jsonb_typeof(violations_by_rule) = 'object'), "
        "ADD COLUMN matches_removed integer NOT NULL DEFAULT 0 CHECK (matches_removed >= 0), "
        "ADD COLUMN excluded_rows_removed integer NOT NULL DEFAULT 0 "
        "CHECK (excluded_rows_removed >= 0)"
    )
    op.execute(
        "ALTER TABLE ingest_runs "
        "ALTER COLUMN contract_version DROP DEFAULT, "
        "ALTER COLUMN violations_by_rule DROP DEFAULT, "
        "ALTER COLUMN matches_removed DROP DEFAULT, "
        "ALTER COLUMN excluded_rows_removed DROP DEFAULT"
    )
    op.execute("ALTER TABLE excluded_rows DROP CONSTRAINT excluded_rows_reason_check")
    op.execute(
        "ALTER TABLE excluded_rows ADD CONSTRAINT excluded_rows_reason_check "
        f"CHECK (reason IN ({_NEW_REASONS}))"
    )


def downgrade() -> None:
    # Restoring the six-code CHECK (validated) fails, and this revision's transaction is rolled
    # back, while contract_violation rows exist: exclusion records are never silently deleted.
    op.execute("ALTER TABLE excluded_rows DROP CONSTRAINT excluded_rows_reason_check")
    op.execute(
        "ALTER TABLE excluded_rows ADD CONSTRAINT excluded_rows_reason_check "
        f"CHECK (reason IN ({_OLD_REASONS}))"
    )
    op.execute(
        "ALTER TABLE ingest_runs "
        "DROP COLUMN contract_version, DROP COLUMN violations_by_rule, "
        "DROP COLUMN matches_removed, DROP COLUMN excluded_rows_removed"
    )
