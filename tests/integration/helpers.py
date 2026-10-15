"""Database helpers for integration tests (pure SQL, no pg_dump)."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.types.json import Jsonb

from football_stats import clean, extract, validate
from football_stats.data_contract import PRE_CONTRACT
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
