import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import polars as pl
import psycopg
import pytest
from psycopg import errors

from football_stats import clean
from football_stats.data_contract import CURRENT_CONTRACT, PRE_CONTRACT, find_violations
from football_stats.source_contract import MATCH_KEY_COLUMNS, MATCH_STAGING_COLUMNS
from tests.conftest import fixture_path
from tests.contract_cases import CASE_IDS, CONTRACT_CASES, ContractCase, materialize
from tests.stages import partition_file, partition_rows

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


# --- data contract parity (FR-007, research.md R13) ---------------------------------------------

_NON_KEY = tuple(c for c in MATCH_STAGING_COLUMNS if c not in MATCH_KEY_COLUMNS)
_INSERT_COLUMNS = ("league_code", "match_date", "home_team_id", "away_team_id", *_NON_KEY)
_PREFIX = "matches_contract_"


def _db_utc_date(conn: psycopg.Connection) -> dt.date:
    row = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()
    assert row is not None
    value: dt.date = row[0]
    return value


def _insert_staged(conn: psycopg.Connection, table: str, matches: pl.DataFrame) -> None:
    """Insert staged match rows into ``table`` with literal team ids 1 and 2.

    No contract expression reads team ids, and ``LIKE`` copies no foreign key or
    ``home <> away`` CHECK, so literal ids are enough for the probe table (plan review 3, minor 2).
    """
    placeholders = ", ".join(["%s"] * len(_INSERT_COLUMNS))
    query = f"INSERT INTO {table} ({', '.join(_INSERT_COLUMNS)}) VALUES ({placeholders})"
    for row in matches.iter_rows(named=True):
        values = [row["league_code"], row["match_date"], 1, 2, *(row[c] for c in _NON_KEY)]
        conn.execute(query.encode(), values)


def _probe_table(conn: psycopg.Connection) -> None:
    """Constraint-free copy of ``matches`` (defaults and identity, but no CHECKs)."""
    conn.execute(
        "CREATE TEMP TABLE probe (LIKE matches INCLUDING DEFAULTS INCLUDING IDENTITY) "
        "ON COMMIT DROP"
    )


def _failing_rules(conn: psycopg.Connection) -> dict[int, set[str]]:
    """Evaluate every ``matches_contract_*`` expression on ``probe``; map row -> failed rules."""
    constraints = conn.execute(
        "SELECT conname, pg_get_expr(conbin, conrelid) FROM pg_constraint "
        "WHERE conrelid = 'matches'::regclass AND conname LIKE 'matches_contract_%'"
    ).fetchall()
    failing: dict[int, set[str]] = {}
    for name, expression in constraints:
        rows = conn.execute(
            f"SELECT source_row_number FROM probe WHERE ({expression}) IS FALSE".encode()
        ).fetchall()
        for (number,) in rows:
            failing.setdefault(number, set()).add(name.removeprefix(_PREFIX))
    return failing


def _stage(rows: list[dict[str, str | None]], run_date: dt.date) -> pl.DataFrame:
    part = partition_rows(rows, contract=PRE_CONTRACT, run_date=run_date)
    assert part.excluded.height == 0, part.excluded
    return clean.build_batch(part.accepted, SHA).matches


@pytest.mark.parametrize("case", CONTRACT_CASES, ids=CASE_IDS)
def test_db_agrees_with_pipeline_on_contract_case(
    case: ContractCase, seeded: psycopg.Connection
) -> None:
    run_date = _db_utc_date(seeded)
    row = materialize(case, run_date)
    staged = _stage([row], run_date)
    _probe_table(seeded)
    _insert_staged(seeded, "probe", staged)
    assert _failing_rules(seeded) == ({1: set(case.violated)} if case.violated else {})

    inserted = True
    try:
        with seeded.transaction():
            _insert_staged(seeded, "matches", staged)
    except errors.CheckViolation:
        inserted = False
    assert inserted is not bool(case.violated)

    accepted = partition_rows([row], run_date=run_date).accepted.height == 1
    assert accepted is inserted


@pytest.mark.parametrize(
    "name", ["matches_contract.csv", "matches_edge_cases.csv", "matches_real_sample.csv"]
)
def test_db_constraints_agree_with_pipeline_on_fixture_rows(
    name: str, seeded: psycopg.Connection
) -> None:
    run_date = _db_utc_date(seeded)
    part = partition_file(fixture_path(name), contract=PRE_CONTRACT, run_date=run_date)
    staged = clean.build_batch(part.accepted, SHA).matches
    _probe_table(seeded)
    _insert_staged(seeded, "probe", staged)

    expected: dict[int, set[str]] = {}
    for number, rule_id in find_violations(part.accepted, CURRENT_CONTRACT, run_date).iter_rows():
        expected.setdefault(number, set()).add(rule_id)
    assert _failing_rules(seeded) == expected


def test_contract_cases_cover_every_rule() -> None:
    all_rules = set(CURRENT_CONTRACT.rule_ids)
    assert {rule for case in CONTRACT_CASES for rule in case.violated} == all_rules
    for rule in all_rules:
        assert any(case.violated == (rule,) for case in CONTRACT_CASES), rule


def test_contract_constraint_rejects_violating_update(seeded: psycopg.Connection) -> None:
    run_date = _db_utc_date(seeded)
    valid = next(case for case in CONTRACT_CASES if case.case_id == "valid_full")
    _insert_staged(seeded, "matches", _stage([materialize(valid, run_date)], run_date))
    for assignment, constraint in (
        ("ht_home_goals = 9", "matches_contract_ht_le_ft_home"),
        ("max_odd_home = 1.0", "matches_contract_max_odds_ge_avg"),
    ):
        with pytest.raises(errors.CheckViolation) as info, seeded.transaction():
            seeded.execute(f"UPDATE matches SET {assignment}".encode())
        assert info.value.diag.constraint_name == constraint
