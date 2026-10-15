"""Load stage: one transaction of COPY into temp tables plus ``ON CONFLICT`` upserts."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import polars as pl
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from football_stats.clean import CleanBatch
from football_stats.errors import AccountingError, ClockSkewError, ConcurrentRunError, LoadError
from football_stats.source_contract import (
    MATCH_KEY_COLUMNS,
    MATCH_STAGING_COLUMNS,
    MATCH_STAGING_SQL_TYPES,
)

logger = logging.getLogger(__name__)

#: Arbitrary constant key of the advisory lock that serialises ingest runs.
LOCK_KEY = 7_203_001_001

_NON_KEY_COLUMNS = tuple(c for c in MATCH_STAGING_COLUMNS if c not in MATCH_KEY_COLUMNS)


@dataclass(frozen=True)
class RunMeta:
    """Facts about a run that are recorded in ``ingest_runs``."""

    started_at: dt.datetime
    source_url: str
    source_sha256: str
    source_bytes: int
    raw_path: str
    rows_read: int
    rows_excluded: int
    excluded_by_reason: dict[str, int]
    run_date: dt.date
    contract_version: int
    violations_by_rule: dict[str, int]


@dataclass(frozen=True)
class LoadCounts:
    """Outcome of a successful load."""

    run_id: int
    matches_inserted: int
    matches_updated: int
    matches_unchanged: int
    matches_removed: int = 0
    excluded_rows_removed: int = 0


def _idents(columns: tuple[str, ...], prefix: str = "") -> sql.Composable:
    return sql.SQL(", ").join(sql.SQL(prefix) + sql.Identifier(c) for c in columns)


def _create_staging(cur: psycopg.Cursor) -> None:
    columns = sql.SQL(", ").join(
        sql.Identifier(c) + sql.SQL(" " + MATCH_STAGING_SQL_TYPES[c]) for c in MATCH_STAGING_COLUMNS
    )
    cur.execute(sql.SQL("CREATE TEMP TABLE stg_matches ({}) ON COMMIT DROP").format(columns))
    cur.execute("CREATE TEMP TABLE stg_teams (team_key text, name text) ON COMMIT DROP")
    cur.execute(
        "CREATE TEMP TABLE stg_excluded (source_row_number integer, reason text, "
        "detail text, raw_record jsonb) ON COMMIT DROP"
    )


def _copy_frame(
    cur: psycopg.Cursor, table: str, columns: tuple[str, ...], frame: pl.DataFrame
) -> None:
    query = sql.SQL("COPY {} ({}) FROM STDIN (FORMAT csv)").format(
        sql.Identifier(table), _idents(columns)
    )
    with cur.copy(query) as copy:
        copy.write(frame.select(columns).write_csv(include_header=False))


def _upsert_matches_sql() -> sql.Composed:
    insert_columns = (
        "league_code",
        "match_date",
        "home_team_id",
        "away_team_id",
        *_NON_KEY_COLUMNS,
    )
    select_list = sql.SQL(", ").join(
        [
            sql.SQL("s.league_code"),
            sql.SQL("s.match_date"),
            sql.SQL("h.team_id"),
            sql.SQL("a.team_id"),
            *(sql.SQL("s.") + sql.Identifier(c) for c in _NON_KEY_COLUMNS),
        ]
    )
    assignments = sql.SQL(", ").join(
        sql.Identifier(c) + sql.SQL(" = EXCLUDED.") + sql.Identifier(c) for c in _NON_KEY_COLUMNS
    )
    old = sql.SQL(", ").join(sql.SQL("matches.") + sql.Identifier(c) for c in _NON_KEY_COLUMNS)
    new = sql.SQL(", ").join(sql.SQL("EXCLUDED.") + sql.Identifier(c) for c in _NON_KEY_COLUMNS)
    return sql.SQL(
        "INSERT INTO matches ({insert_columns}) "
        "SELECT {select_list} FROM stg_matches s "
        "JOIN teams h ON h.team_key = s.home_team_key "
        "JOIN teams a ON a.team_key = s.away_team_key "
        "ORDER BY s.source_row_number "
        "ON CONFLICT ON CONSTRAINT matches_natural_key DO UPDATE SET {assignments} "
        "WHERE ({old}) IS DISTINCT FROM ({new}) "
        "RETURNING (xmax = 0) AS inserted"
    ).format(
        insert_columns=_idents(insert_columns),
        select_list=select_list,
        assignments=assignments,
        old=old,
        new=new,
    )


def _copy_excluded(cur: psycopg.Cursor, excluded: pl.DataFrame) -> None:
    """COPY the excluded rows into ``stg_excluded`` (used by convergence and the upsert)."""
    with cur.copy(
        "COPY stg_excluded (source_row_number, reason, detail, raw_record) FROM STDIN"
    ) as copy:
        for number, reason, detail, raw_record in excluded.select(
            "source_row_number", "reason", "detail", "raw_record"
        ).iter_rows():
            copy.write_row((number, reason, detail, Jsonb(raw_record)))


def _persist_excluded(cur: psycopg.Cursor, run_id: int, source_sha256: str) -> None:
    """Upsert ``stg_excluded`` keyed by ``(source_sha256, source_row_number)`` (R11 step 7).

    ``run_id`` stays the first run that recorded the row: it is never updated.
    """
    cur.execute(
        "INSERT INTO excluded_rows (run_id, source_sha256, source_row_number, reason, detail, "
        "raw_record) SELECT %s, %s, source_row_number, reason, detail, raw_record "
        "FROM stg_excluded ORDER BY source_row_number "
        "ON CONFLICT ON CONSTRAINT excluded_rows_source_key DO UPDATE SET "
        "reason = EXCLUDED.reason, detail = EXCLUDED.detail, raw_record = EXCLUDED.raw_record "
        "WHERE (excluded_rows.reason, excluded_rows.detail, excluded_rows.raw_record) "
        "IS DISTINCT FROM (EXCLUDED.reason, EXCLUDED.detail, EXCLUDED.raw_record)",
        (run_id, source_sha256),
    )


#: Matches of this source file without a staged row of the same natural key and row number
#: (002 research.md R10).
_DELETE_STALE_MATCHES = (
    "DELETE FROM matches m WHERE m.source_sha256 = %s AND NOT EXISTS ("
    "SELECT 1 FROM stg_matches s "
    "JOIN teams h ON h.team_key = s.home_team_key "
    "JOIN teams a ON a.team_key = s.away_team_key "
    "WHERE s.source_row_number = m.source_row_number "
    "AND s.league_code = m.league_code AND s.match_date = m.match_date "
    "AND h.team_id = m.home_team_id AND a.team_id = m.away_team_id)"
)
_DELETE_STALE_EXCLUDED = (
    "DELETE FROM excluded_rows e WHERE e.source_sha256 = %s AND NOT EXISTS ("
    "SELECT 1 FROM stg_excluded s WHERE s.source_row_number = e.source_row_number)"
)
_DELETE_ORPHAN_TEAMS = (
    "DELETE FROM teams t WHERE NOT EXISTS (SELECT 1 FROM matches WHERE home_team_id = t.team_id) "
    "AND NOT EXISTS (SELECT 1 FROM matches WHERE away_team_id = t.team_id)"
)
_DELETE_ORPHAN_LEAGUES = (
    "DELETE FROM leagues l "
    "WHERE NOT EXISTS (SELECT 1 FROM matches WHERE league_code = l.league_code)"
)


def _converge(cur: psycopg.Cursor, source_sha256: str) -> tuple[int, int]:
    """Make the database equal a fresh build of this file (FR-009, research.md R10, R11).

    Returns:
        ``(matches_removed, excluded_rows_removed)``.
    """
    cur.execute(_DELETE_STALE_MATCHES.encode(), (source_sha256,))
    matches_removed = cur.rowcount
    cur.execute(_DELETE_STALE_EXCLUDED.encode(), (source_sha256,))
    excluded_rows_removed = cur.rowcount
    cur.execute(_DELETE_ORPHAN_TEAMS.encode())
    teams_removed = cur.rowcount
    cur.execute(_DELETE_ORPHAN_LEAGUES.encode())
    leagues_removed = cur.rowcount
    logger.info(
        "converge matches_removed=%d excluded_rows_removed=%d teams_removed=%d leagues_removed=%d",
        matches_removed,
        excluded_rows_removed,
        teams_removed,
        leagues_removed,
    )
    return matches_removed, excluded_rows_removed


def _check_clock(cur: psycopg.Cursor, run_date: dt.date) -> None:
    """Refuse to start if the database's UTC date is earlier than the run date (R8).

    The ``date_in_range`` CHECK uses the database's UTC date as its upper bound; if that were
    earlier than the pipeline's bound the database could reject a row the pipeline accepted,
    halfway through the load. Detect it before any write instead.
    """
    row = cur.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()
    if row is None:
        raise LoadError("could not read the database date")
    db_date = row[0]
    if db_date < run_date:
        raise ClockSkewError(f"database UTC date {db_date} is earlier than run date {run_date}")


def load_run(
    conn: psycopg.Connection,
    batch: CleanBatch,
    excluded: pl.DataFrame,
    run: RunMeta,
) -> LoadCounts:
    """Load one run in a single transaction (research.md R11).

    Args:
        conn: Open connection (not in a transaction).
        batch: Leagues, teams and matches to upsert.
        excluded: Excluded rows to persist (``source_row_number``, ``reason``, ``detail``,
            ``raw_record`` as a dict of every source column).
        run: Run metadata for ``ingest_runs``.

    Returns:
        The run id and the inserted/updated/unchanged/removed counts.

    Raises:
        ConcurrentRunError: If another run holds the advisory lock.
        LoadError: If any database statement fails (the transaction is rolled back).
    """
    try:
        with conn.transaction(), conn.cursor() as cur:
            locked = cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (LOCK_KEY,)).fetchone()
            if locked is None or not locked[0]:
                raise ConcurrentRunError("Another ingest run is in progress")
            _check_clock(cur, run.run_date)
            _create_staging(cur)
            _copy_frame(cur, "stg_matches", MATCH_STAGING_COLUMNS, batch.matches)
            _copy_frame(cur, "stg_teams", ("team_key", "name"), batch.teams)
            _copy_excluded(cur, excluded)
            # COPY leaves temp tables without statistics; the convergence anti-joins need them.
            cur.execute("ANALYZE stg_matches, stg_excluded")

            cur.executemany(
                "INSERT INTO leagues (league_code) VALUES (%s) ON CONFLICT DO NOTHING",
                [(code,) for code in batch.leagues["league_code"]],
            )
            cur.execute(
                "INSERT INTO teams (team_key, name) SELECT team_key, name FROM stg_teams "
                "ORDER BY team_key ON CONFLICT (team_key) DO UPDATE SET name = EXCLUDED.name "
                "WHERE teams.name IS DISTINCT FROM EXCLUDED.name"
            )
            joined = cur.execute(
                "SELECT count(*) FROM stg_matches s "
                "JOIN teams h ON h.team_key = s.home_team_key "
                "JOIN teams a ON a.team_key = s.away_team_key"
            ).fetchone()
            if joined is None or joined[0] != batch.matches.height:
                raise AccountingError(
                    f"{batch.matches.height} staged matches but {joined and joined[0]} resolve "
                    "to two known teams; refusing to load"
                )
            # The SET list is every non-key column (research.md R9): the rewritten tuple equals the
            # staged tuple, so an existing violating row can never trip a NOT VALID constraint.
            cur.execute(_upsert_matches_sql())
            # `xmax = 0` identifies freshly inserted tuples: a widely used Postgres idiom that
            # relies on an implementation detail. It is guarded by
            # test_second_run_reports_all_unchanged and test_changed_source_updates_match_in_place.
            returned = [bool(row[0]) for row in cur.fetchall()]
            inserted = sum(returned)
            updated = len(returned) - inserted
            unchanged = batch.matches.height - len(returned)

            matches_removed, excluded_rows_removed = _converge(cur, run.source_sha256)

            row = cur.execute(
                "INSERT INTO ingest_runs (started_at, finished_at, source_url, source_sha256, "
                "source_bytes, raw_path, rows_read, rows_loaded, rows_excluded, "
                "excluded_by_reason, contract_version, violations_by_rule, matches_inserted, "
                "matches_updated, matches_unchanged, matches_removed, excluded_rows_removed) "
                "VALUES (%s, clock_timestamp(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s) RETURNING run_id",
                (
                    run.started_at,
                    run.source_url,
                    run.source_sha256,
                    run.source_bytes,
                    run.raw_path,
                    run.rows_read,
                    batch.matches.height,
                    run.rows_excluded,
                    Jsonb(run.excluded_by_reason),
                    run.contract_version,
                    Jsonb(run.violations_by_rule),
                    inserted,
                    updated,
                    unchanged,
                    matches_removed,
                    excluded_rows_removed,
                ),
            ).fetchone()
            if row is None:
                raise LoadError("ingest_runs insert returned no run_id")
            run_id = int(row[0])
            _persist_excluded(cur, run_id, run.source_sha256)
    except psycopg.Error as exc:
        raise LoadError(f"Database load failed and was rolled back: {exc}") from exc
    return LoadCounts(run_id, inserted, updated, unchanged, matches_removed, excluded_rows_removed)
