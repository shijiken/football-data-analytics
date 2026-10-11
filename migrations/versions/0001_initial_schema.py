"""Initial schema: leagues, teams, matches, ingest_runs, excluded_rows (data-model.md section 1).

Revision ID: 0001_initial_schema
Revises:
"""

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE leagues (
            league_code text PRIMARY KEY CHECK (league_code ~ '^[A-Z0-9]{1,10}$')
        )
        """
    )
    op.execute(
        """
        CREATE TABLE teams (
            team_id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            team_key text NOT NULL UNIQUE CHECK (team_key <> ''),
            name text NOT NULL CHECK (btrim(name) <> '')
        )
        """
    )
    op.execute(
        """
        CREATE TABLE matches (
            match_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            league_code text NOT NULL REFERENCES leagues (league_code),
            match_date date NOT NULL,
            kickoff_time time,
            home_team_id integer NOT NULL REFERENCES teams (team_id),
            away_team_id integer NOT NULL REFERENCES teams (team_id),
            source_sha256 char(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
            source_row_number integer NOT NULL CHECK (source_row_number >= 1),
            ft_home_goals smallint NOT NULL CHECK (ft_home_goals >= 0),
            ft_away_goals smallint NOT NULL CHECK (ft_away_goals >= 0),
            ft_result char(1) NOT NULL CHECK (ft_result IN ('H','D','A')),
        home_elo double precision,
        away_elo double precision,
        home_form3 smallint CHECK (home_form3 >= 0),
        home_form5 smallint CHECK (home_form5 >= 0),
        away_form3 smallint CHECK (away_form3 >= 0),
        away_form5 smallint CHECK (away_form5 >= 0),
        ht_home_goals smallint CHECK (ht_home_goals >= 0),
        ht_away_goals smallint CHECK (ht_away_goals >= 0),
        ht_result char(1) CHECK (ht_result IN ('H','D','A')),
        home_shots smallint CHECK (home_shots >= 0),
        away_shots smallint CHECK (away_shots >= 0),
        home_shots_on_target smallint CHECK (home_shots_on_target >= 0),
        away_shots_on_target smallint CHECK (away_shots_on_target >= 0),
        home_fouls smallint CHECK (home_fouls >= 0),
        away_fouls smallint CHECK (away_fouls >= 0),
        home_corners smallint CHECK (home_corners >= 0),
        away_corners smallint CHECK (away_corners >= 0),
        home_yellow_cards smallint CHECK (home_yellow_cards >= 0),
        away_yellow_cards smallint CHECK (away_yellow_cards >= 0),
        home_red_cards smallint CHECK (home_red_cards >= 0),
        away_red_cards smallint CHECK (away_red_cards >= 0),
        odd_home double precision,
        odd_draw double precision,
        odd_away double precision,
        max_odd_home double precision,
        max_odd_draw double precision,
        max_odd_away double precision,
        odd_over25 double precision,
        odd_under25 double precision,
        max_odd_over25 double precision,
        max_odd_under25 double precision,
        handicap_size double precision,
        handicap_odd_home double precision,
        handicap_odd_away double precision,
        cluster_lth double precision,
        cluster_lta double precision,
        cluster_vhd double precision,
        cluster_vad double precision,
        cluster_htb double precision,
        cluster_phb double precision,
            CHECK (home_team_id <> away_team_id),
            CONSTRAINT matches_natural_key
                UNIQUE (league_code, match_date, home_team_id, away_team_id)
        )
        """
    )
    op.execute("CREATE INDEX matches_home_team_id_idx ON matches (home_team_id)")
    op.execute("CREATE INDEX matches_away_team_id_idx ON matches (away_team_id)")
    op.execute(
        """
        CREATE TABLE ingest_runs (
            run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            started_at timestamptz NOT NULL,
            finished_at timestamptz NOT NULL CHECK (finished_at >= started_at),
            source_url text NOT NULL,
            source_sha256 char(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
            source_bytes bigint NOT NULL CHECK (source_bytes >= 0),
            raw_path text NOT NULL,
            rows_read integer NOT NULL CHECK (rows_read >= 0),
            rows_loaded integer NOT NULL CHECK (rows_loaded >= 0),
            rows_excluded integer NOT NULL CHECK (rows_excluded >= 0),
            excluded_by_reason jsonb NOT NULL,
            matches_inserted integer NOT NULL CHECK (matches_inserted >= 0),
            matches_updated integer NOT NULL CHECK (matches_updated >= 0),
            matches_unchanged integer NOT NULL CHECK (matches_unchanged >= 0),
            CHECK (rows_read = rows_loaded + rows_excluded),
            CHECK (rows_loaded = matches_inserted + matches_updated + matches_unchanged)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE excluded_rows (
            excluded_row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            run_id bigint NOT NULL REFERENCES ingest_runs (run_id),
            source_sha256 char(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
            source_row_number integer NOT NULL CHECK (source_row_number >= 1),
            reason text NOT NULL CHECK (reason IN (
                'missing_field', 'invalid_date', 'missing_result',
                'invalid_value', 'same_team', 'duplicate'
            )),
            detail text NOT NULL,
            raw_record jsonb NOT NULL,
            CONSTRAINT excluded_rows_source_key UNIQUE (source_sha256, source_row_number)
        )
        """
    )
    op.execute("CREATE INDEX excluded_rows_run_id_idx ON excluded_rows (run_id)")


def downgrade() -> None:
    op.execute("DROP TABLE excluded_rows")
    op.execute("DROP TABLE ingest_runs")
    op.execute("DROP TABLE matches")
    op.execute("DROP TABLE teams")
    op.execute("DROP TABLE leagues")
