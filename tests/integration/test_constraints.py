from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest
from psycopg import errors

SHA = "a" * 64


@contextmanager
def _expect(conn: psycopg.Connection, exc: type[psycopg.Error]) -> Iterator[None]:
    """Expect ``exc`` inside a savepoint so the connection stays usable."""
    with pytest.raises(exc), conn.transaction():
        yield


def _league(conn: psycopg.Connection, code: str = "E0") -> None:
    conn.execute("INSERT INTO leagues (league_code) VALUES (%s)", (code,))


def _team(conn: psycopg.Connection, key: str) -> int:
    row = conn.execute(
        "INSERT INTO teams (team_key, name) VALUES (%s, %s) RETURNING team_id", (key, key.title())
    ).fetchone()
    assert row is not None
    return int(row[0])


def _match(conn: psycopg.Connection, **overrides: Any) -> None:
    values: dict[str, Any] = {
        "league_code": "E0",
        "match_date": "2020-09-12",
        "home_team_id": 1,
        "away_team_id": 2,
        "ft_home_goals": 1,
        "ft_away_goals": 0,
        "ft_result": "H",
        "source_sha256": SHA,
        "source_row_number": 1,
    }
    values.update(overrides)
    columns = list(values)
    conn.execute(
        "INSERT INTO matches ("
        + ", ".join(columns)
        + ") VALUES ("
        + ", ".join(f"%({c})s" for c in columns)
        + ")",
        values,
    )


@pytest.fixture
def seeded(db_conn: psycopg.Connection) -> psycopg.Connection:
    """Connection with one league and two teams (ids 1 and 2)."""
    _league(db_conn)
    assert _team(db_conn, "arsenal") == 1
    assert _team(db_conn, "chelsea") == 2
    return db_conn


def test_constraint_rejects_bad_league_code(db_conn: psycopg.Connection) -> None:
    for code in ("e0", "", "E 0", "ABCDEFGHIJK"):
        with _expect(db_conn, errors.CheckViolation):
            _league(db_conn, code)


def test_constraint_rejects_blank_team(db_conn: psycopg.Connection) -> None:
    with _expect(db_conn, errors.CheckViolation):
        db_conn.execute("INSERT INTO teams (team_key, name) VALUES ('', 'Ajax')")
    with _expect(db_conn, errors.CheckViolation):
        db_conn.execute("INSERT INTO teams (team_key, name) VALUES ('ajax', '   ')")


def test_constraint_rejects_home_equals_away(seeded: psycopg.Connection) -> None:
    with _expect(seeded, errors.CheckViolation):
        _match(seeded, home_team_id=1, away_team_id=1)


def test_constraint_rejects_duplicate_natural_key(seeded: psycopg.Connection) -> None:
    _match(seeded)
    with _expect(seeded, errors.UniqueViolation):
        _match(seeded, source_row_number=2)


@pytest.mark.parametrize("column", ["match_date", "home_team_id", "ft_home_goals", "ft_result"])
def test_constraint_rejects_null_required(seeded: psycopg.Connection, column: str) -> None:
    with _expect(seeded, errors.NotNullViolation):
        _match(seeded, **{column: None})


def test_constraint_rejects_negative_goals(seeded: psycopg.Connection) -> None:
    with _expect(seeded, errors.CheckViolation):
        _match(seeded, ft_home_goals=-1)
    with _expect(seeded, errors.CheckViolation):
        _match(seeded, home_shots=-1)


def test_constraint_rejects_bad_result(seeded: psycopg.Connection) -> None:
    with _expect(seeded, errors.CheckViolation):
        _match(seeded, ft_result="X")
    with _expect(seeded, errors.CheckViolation):
        _match(seeded, ht_result="h")


def test_constraint_rejects_unknown_league(seeded: psycopg.Connection) -> None:
    with _expect(seeded, errors.ForeignKeyViolation):
        _match(seeded, league_code="ZZ")


def test_constraint_rejects_unknown_team(seeded: psycopg.Connection) -> None:
    with _expect(seeded, errors.ForeignKeyViolation):
        _match(seeded, away_team_id=999)


def _run(conn: psycopg.Connection, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "source_url": "file:///x.csv",
        "source_sha256": SHA,
        "source_bytes": 10,
        "raw_path": "/x",
        "rows_read": 10,
        "rows_loaded": 6,
        "rows_excluded": 4,
        "matches_inserted": 4,
        "matches_updated": 1,
        "matches_unchanged": 1,
        "contract_version": 1,
        "matches_removed": 0,
        "excluded_rows_removed": 0,
    }
    values.update(overrides)
    values.setdefault("violations_by_rule", "{}")
    cols = [c for c in values if c != "violations_by_rule"]
    return conn.execute(
        "INSERT INTO ingest_runs (started_at, finished_at, excluded_by_reason, "
        "violations_by_rule, " + ", ".join(cols) + ") VALUES (now(), now(), '{}'::jsonb, "
        "%(violations_by_rule)s::jsonb, "
        + ", ".join(f"%({c})s" for c in cols)
        + ") RETURNING run_id",
        values,
    )


def test_ingest_runs_check_rejects_unbalanced_counts(db_conn: psycopg.Connection) -> None:
    _run(db_conn)  # balanced row is accepted
    with _expect(db_conn, errors.CheckViolation):
        _run(db_conn, rows_excluded=3)  # read != loaded + excluded
    with _expect(db_conn, errors.CheckViolation):
        _run(db_conn, matches_unchanged=0)  # loaded != inserted + updated + unchanged


def test_excluded_rows_reason_check(db_conn: psycopg.Connection) -> None:
    row = _run(db_conn).fetchone()
    assert row is not None
    run_id = row[0]

    def insert(reason: str, n: int) -> None:
        db_conn.execute(
            "INSERT INTO excluded_rows (run_id, source_sha256, source_row_number, reason, "
            "detail, raw_record) VALUES (%s, %s, %s, %s, 'd', '{}'::jsonb)",
            (run_id, SHA, n, reason),
        )

    insert("missing_field", 1)
    insert("contract_violation", 3)
    with _expect(db_conn, errors.CheckViolation):
        insert("bogus", 2)


def test_ingest_runs_contract_columns_checks(db_conn: psycopg.Connection) -> None:
    for column, bad in (
        ("contract_version", -1),
        ("matches_removed", -1),
        ("excluded_rows_removed", -1),
        ("violations_by_rule", "[]"),
    ):
        with _expect(db_conn, errors.CheckViolation):
            _run(db_conn, **{column: bad})
    with _expect(db_conn, errors.NotNullViolation):
        db_conn.execute(
            "INSERT INTO ingest_runs (started_at, finished_at, source_url, source_sha256, "
            "source_bytes, raw_path, rows_read, rows_loaded, rows_excluded, excluded_by_reason, "
            "matches_inserted, matches_updated, matches_unchanged, violations_by_rule, "
            "matches_removed, excluded_rows_removed) VALUES (now(), now(), 'u', %s, 1, 'p', "
            "0, 0, 0, '{}'::jsonb, 0, 0, 0, '{}'::jsonb, 0, 0)",
            (SHA,),
        )
