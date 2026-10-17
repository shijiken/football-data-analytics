"""Database helpers for integration tests (pure SQL, no pg_dump)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from football_stats import clean, extract, pipeline, validate
from football_stats.config import Settings
from football_stats.data_contract import CURRENT_CONTRACT, PRE_CONTRACT
from football_stats.source_contract import MATCH_KEY_COLUMNS, MATCH_STAGING_COLUMNS

REPO_ROOT = Path(__file__).resolve().parents[2]

_SNAPSHOT_QUERIES: dict[str, str] = {
    "leagues": "SELECT * FROM leagues ORDER BY league_code",
    "teams": "SELECT * FROM teams ORDER BY team_id",
    "matches": "SELECT * FROM matches ORDER BY match_id",
    "excluded_rows": "SELECT * FROM excluded_rows ORDER BY excluded_row_id",
    "ingest_runs": (
        "SELECT run_id, source_url, source_sha256, source_bytes, raw_path, rows_read, "
        "rows_loaded, rows_excluded, excluded_by_reason, contract_version, violations_by_rule, "
        "matches_inserted, matches_updated, matches_unchanged, matches_removed, "
        "excluded_rows_removed FROM ingest_runs ORDER BY run_id"
    ),
}


def alembic_config(database_url: str) -> Config:
    """Build an Alembic config pointing at ``database_url``."""
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.attributes["database_url"] = database_url
    return cfg


def upgrade(database_url: str, revision: str = "head") -> None:
    """Run ``alembic upgrade`` programmatically."""
    command.upgrade(alembic_config(database_url), revision)


def downgrade(database_url: str, revision: str = "base") -> None:
    """Run ``alembic downgrade`` programmatically."""
    command.downgrade(alembic_config(database_url), revision)


def snapshot(conn: psycopg.Connection) -> dict[str, list[tuple[object, ...]]]:
    """Return all rows of the data tables, ordered by primary key.

    ``ingest_runs`` omits ``started_at``/``finished_at`` (never asserted).
    """
    return {
        table: [tuple(row) for row in conn.execute(query.encode()).fetchall()]
        for table, query in _SNAPSHOT_QUERIES.items()
    }


def table_counts(conn: psycopg.Connection) -> dict[str, int]:
    """Return the row count of every data table."""
    counts: dict[str, int] = {}
    for table in _SNAPSHOT_QUERIES:
        row = conn.execute(f"SELECT count(*) FROM {table}".encode()).fetchone()
        assert row is not None
        counts[table] = int(row[0])
    return counts


def catalog_snapshot(conn: psycopg.Connection) -> dict[str, list[tuple[object, ...]]]:
    """Return ordered catalog rows (columns, constraints, indexes) of schema ``public``."""
    queries = {
        "columns": (
            "SELECT table_name, column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema = 'public' "
            "ORDER BY table_name, column_name"
        ),
        "constraints": (
            "SELECT c.relname, k.conname, k.contype, pg_get_constraintdef(k.oid) "
            "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
            "ORDER BY c.relname, k.conname"
        ),
        "indexes": (
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' ORDER BY tablename, indexname"
        ),
    }
    return {
        name: [tuple(row) for row in conn.execute(query.encode()).fetchall()]
        for name, query in queries.items()
    }


_CONTENT_QUERIES: dict[str, str] = {
    "leagues": "SELECT league_code FROM leagues ORDER BY 1",
    "teams": "SELECT team_key, name FROM teams ORDER BY team_key",
    "matches": (
        "SELECT (to_jsonb(m) - 'match_id' - 'home_team_id' - 'away_team_id') "
        "|| jsonb_build_object('home_team_key', h.team_key, 'away_team_key', a.team_key) "
        "FROM matches m JOIN teams h ON h.team_id = m.home_team_id "
        "JOIN teams a ON a.team_id = m.away_team_id "
        "ORDER BY m.league_code, m.match_date, h.team_key, a.team_key"
    ),
    "excluded_rows": (
        "SELECT to_jsonb(e) - 'excluded_row_id' - 'run_id' FROM excluded_rows e "
        "ORDER BY source_sha256, source_row_number"
    ),
}


def content_snapshot(conn: psycopg.Connection) -> dict[str, list[object]]:
    """Return table contents without surrogate ids or run links (research.md R12).

    Two databases are "identical" for the data contract when these snapshots are equal.
    ``ingest_runs`` is an append-only audit log and is not compared.
    """
    result: dict[str, list[object]] = {}
    for table, query in _CONTENT_QUERIES.items():
        rows = conn.execute(query.encode()).fetchall()
        result[table] = [row[0] if len(row) == 1 else tuple(row) for row in rows]
    return result


_NON_KEY_COLUMNS = tuple(c for c in MATCH_STAGING_COLUMNS if c not in MATCH_KEY_COLUMNS)
_MATCH_INSERT_COLUMNS = (
    "league_code",
    "match_date",
    "home_team_id",
    "away_team_id",
    *_NON_KEY_COLUMNS,
)


def seed_pre_contract(database_url: str, source: Path, run_date: dt.date) -> str:
    """Create the database state feature 001 would have loaded from ``source`` (research.md R13).

    Migrates to ``0001_initial_schema`` and writes leagues, teams, matches, one 0001-shaped
    ``ingest_runs`` row and the excluded rows with plain SQL in one transaction. The pure stages
    run with ``PRE_CONTRACT``, so rows that violate the data contract are loaded as 001 did.

    Returns:
        The SHA-256 of ``source`` (the provenance stamped on the rows).
    """
    upgrade(database_url, "0001_initial_schema")
    parsed = clean.add_team_keys(clean.parse_frame(extract.read_source(source)))
    part = validate.partition(parsed, contract=PRE_CONTRACT, run_date=run_date)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    batch = clean.build_batch(part.accepted, sha)
    by_reason = {k: v for k, v in part.counts_by_reason().items() if k != "contract_violation"}
    insert_match = (
        "INSERT INTO matches ("
        + ", ".join(_MATCH_INSERT_COLUMNS)
        + ") VALUES (%s, %s, (SELECT team_id FROM teams WHERE team_key = %s), "
        "(SELECT team_id FROM teams WHERE team_key = %s)" + ", %s" * len(_NON_KEY_COLUMNS) + ")"
    )
    with psycopg.connect(database_url) as conn, conn.transaction():
        for code in sorted(batch.leagues["league_code"]):
            conn.execute("INSERT INTO leagues (league_code) VALUES (%s)", (code,))
        for key, name in batch.teams.sort("team_key").iter_rows():
            conn.execute("INSERT INTO teams (team_key, name) VALUES (%s, %s)", (key, name))
        for row in batch.matches.sort("source_row_number").iter_rows(named=True):
            values = [
                row["league_code"],
                row["match_date"],
                row["home_team_key"],
                row["away_team_key"],
                *(row[c] for c in _NON_KEY_COLUMNS),
            ]
            conn.execute(insert_match.encode(), values)
        run = conn.execute(
            "INSERT INTO ingest_runs (started_at, finished_at, source_url, source_sha256, "
            "source_bytes, raw_path, rows_read, rows_loaded, rows_excluded, excluded_by_reason, "
            "matches_inserted, matches_updated, matches_unchanged) "
            "VALUES (now(), now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0) RETURNING run_id",
            (
                source.resolve().as_uri(),
                sha,
                source.stat().st_size,
                str(source),
                part.rows_read,
                batch.matches.height,
                part.excluded.height,
                Jsonb(by_reason),
                batch.matches.height,
            ),
        ).fetchone()
        assert run is not None
        for number, reason, detail, raw_record in part.excluded.iter_rows():
            conn.execute(
                "INSERT INTO excluded_rows (run_id, source_sha256, source_row_number, reason, "
                "detail, raw_record) VALUES (%s, %s, %s, %s, %s, %s)",
                (run[0], sha, number, reason, detail, Jsonb(raw_record)),
            )
    return sha


def assert_contract_validates_read_only(db_url: str) -> None:
    """Prove every loaded match satisfies every rule, leaving the catalog untouched (R8)."""
    with psycopg.connect(db_url) as conn:
        for rule_id in CURRENT_CONTRACT.rule_ids:
            conn.execute(
                f"ALTER TABLE matches VALIDATE CONSTRAINT matches_contract_{rule_id}".encode()
            )
        conn.rollback()
        flags = conn.execute(
            "SELECT convalidated FROM pg_constraint WHERE conrelid = 'matches'::regclass "
            "AND conname LIKE 'matches_contract_%'"
        ).fetchall()
    assert flags == [(False,)] * len(CURRENT_CONTRACT.rule_ids)


def load_expected(name: str) -> dict[str, object]:
    """Read a fixture ``*.expected.json`` file (keys are 1-based source row numbers)."""
    path = REPO_ROOT / "tests" / "fixtures" / name
    document: dict[str, object] = json.loads(path.read_text("utf-8"))
    return document


class _ListHandler(logging.Handler):
    """Collect formatted messages of every record it receives."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextmanager
def capture_messages() -> Iterator[list[str]]:
    """Capture INFO+ messages of the ``football_stats`` loggers.

    Used instead of ``caplog`` in tests that run Alembic in-process: ``migrations/env.py`` calls
    ``logging.config.fileConfig``, which replaces the root handlers (including caplog's).
    """
    logger = logging.getLogger("football_stats")
    handler = _ListHandler()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield handler.messages
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


# --- analytics (dbt) helpers, feature 003 -------------------------------------------------------

#: Model name -> ``schema.name`` for the 7 dbt models (data-model.md §2-§3).
ANALYTICS_MODELS: dict[str, str] = {
    "stg_leagues": "analytics_staging.stg_leagues",
    "stg_teams": "analytics_staging.stg_teams",
    "stg_matches": "analytics_staging.stg_matches",
    "mart_team_form": "analytics_marts.mart_team_form",
    "mart_league_season_home_advantage": "analytics_marts.mart_league_season_home_advantage",
    "mart_league_season_goals": "analytics_marts.mart_league_season_goals",
    "mart_team_ratings": "analytics_marts.mart_team_ratings",
}

#: Model name -> ``ORDER BY`` list (the model's key, data-model.md §2-§3).
ANALYTICS_KEYS: dict[str, str] = {
    "stg_leagues": "league_code",
    "stg_teams": "team_id",
    "stg_matches": "match_id",
    "mart_team_form": "match_id, team_id",
    "mart_league_season_home_advantage": "league_code, season",
    "mart_league_season_goals": "league_code, season",
    "mart_team_ratings": "team_id",
}

PublicState = tuple[
    dict[str, list[tuple[object, ...]]],
    dict[str, list[tuple[object, ...]]],
    list[tuple[object, ...]],
]


def ingest_fixture(
    database_url: str, raw_dir: Path, fixture: Path, started_at: dt.datetime
) -> None:
    """Ingest ``fixture`` into ``database_url`` with the real pipeline (raw copy in ``raw_dir``)."""
    pipeline.run(
        Settings(
            database_url=database_url,
            source_url="file:///nonexistent/override-in-test.csv",
            raw_data_dir=raw_dir,
            log_level="DEBUG",
        ),
        source_file=fixture,
        started_at=started_at,
    )


def public_state(conn: psycopg.Connection) -> PublicState:
    """Return ``public`` rows, ``public`` catalog and ``alembic_version`` rows (FR-007)."""
    versions = [
        tuple(row) for row in conn.execute(b"SELECT version_num FROM alembic_version").fetchall()
    ]
    return snapshot(conn), catalog_snapshot(conn), versions


def analytics_snapshot(
    conn: psycopg.Connection, models: Sequence[str] = tuple(ANALYTICS_MODELS)
) -> dict[str, list[object]]:
    """Return every row of the given models as JSON, ordered by each model's key."""
    return {
        model: [
            row[0]
            for row in conn.execute(
                f"SELECT to_jsonb(t) FROM {ANALYTICS_MODELS[model]} t "
                f"ORDER BY {ANALYTICS_KEYS[model]}".encode()
            ).fetchall()
        ]
        for model in models
    }


def analytics_catalog_snapshot(conn: psycopg.Connection) -> dict[str, list[tuple[object, ...]]]:
    """Return the catalog of the analytics schemas with generated names stripped (R11, R12)."""
    schemas = "('analytics_staging', 'analytics_marts')"
    queries = {
        "tables": (
            "SELECT table_schema, table_name, table_type FROM information_schema.tables "
            f"WHERE table_schema IN {schemas} ORDER BY 1, 2"
        ),
        "columns": (
            "SELECT table_schema, table_name, column_name, data_type, numeric_precision, "
            "numeric_scale, is_nullable FROM information_schema.columns "
            f"WHERE table_schema IN {schemas} ORDER BY table_schema, table_name, ordinal_position"
        ),
        "indexes": (
            "SELECT tablename, regexp_replace(indexdef, 'INDEX \\S+ ON', 'INDEX ON') "
            f"FROM pg_indexes WHERE schemaname IN {schemas} ORDER BY 1, 2"
        ),
        "constraints": (
            "SELECT c.relname, k.contype, pg_get_constraintdef(k.oid) FROM pg_constraint k "
            "JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname IN {schemas} ORDER BY 1, 2, 3"
        ),
    }
    return {
        name: [tuple(row) for row in conn.execute(query.encode()).fetchall()]
        for name, query in queries.items()
    }


_MART_ROW_QUERIES: dict[str, str] = {
    "mart_team_form": (
        "SELECT t.team_key, m.source_row_number, o.team_key AS opponent_team_key, f.league_code, "
        "f.season, f.season_start_year, f.match_date::text AS match_date, f.is_home, "
        "f.goals_for, f.goals_against, f.result, f.points, f.team_match_number, "
        "f.form_matches, f.form_points, f.form_goals_for, f.form_goals_against "
        "FROM analytics_marts.mart_team_form f JOIN teams t ON t.team_id = f.team_id "
        "JOIN teams o ON o.team_id = f.opponent_team_id JOIN matches m ON m.match_id = f.match_id "
        "ORDER BY t.team_key, f.match_date, m.source_row_number"
    ),
    "mart_league_season_home_advantage": (
        "SELECT league_code, season, season_start_year, matches_played, home_wins, draws, "
        "away_wins, home_win_share::text AS home_win_share, draw_share::text AS draw_share, "
        "away_win_share::text AS away_win_share, avg_home_goals::text AS avg_home_goals, "
        "avg_away_goals::text AS avg_away_goals "
        "FROM analytics_marts.mart_league_season_home_advantage ORDER BY league_code, season"
    ),
    "mart_league_season_goals": (
        "SELECT league_code, season, season_start_year, matches_played, total_goals, "
        "goals_per_match::text AS goals_per_match, over_2_5_matches, "
        "over_2_5_share::text AS over_2_5_share, btts_matches, btts_share::text AS btts_share "
        "FROM analytics_marts.mart_league_season_goals ORDER BY league_code, season"
    ),
    "mart_team_ratings": (
        "SELECT team_key, team_name, rated_matches, latest_elo, latest_elo_date::text "
        "AS latest_elo_date, peak_elo, peak_elo_date::text AS peak_elo_date, lowest_elo, "
        "lowest_elo_date::text AS lowest_elo_date FROM analytics_marts.mart_team_ratings "
        "ORDER BY team_key"
    ),
}


def mart_rows(conn: psycopg.Connection, mart: str) -> list[dict[str, object]]:
    """Return the rows of ``mart`` with natural keys, numerics as text and dates as ISO text.

    The shape and order match ``tests/fixtures/matches_transform.expected.json``.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        return [dict(row) for row in cur.execute(_MART_ROW_QUERIES[mart].encode()).fetchall()]
