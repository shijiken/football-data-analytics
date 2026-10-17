"""Staging and mart contents against hand-calculated expectations (feature 003, FR-009)."""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from tests.integration import helpers
from tests.integration.conftest import TransformedDb


class _Rollback(Exception):  # a control-flow sentinel, not an error
    """Raised inside a transaction block to force its rollback."""


def _expected(name: str) -> list[dict[str, object]]:
    rows = helpers.load_expected("matches_transform.expected.json")[name]
    assert isinstance(rows, list)
    return rows


def test_fixture_match_ids_follow_source_order(transformed_db: TransformedDb) -> None:
    """On a fresh load ``match_id`` equals the source row number (tie-break premise, R8)."""
    with psycopg.connect(transformed_db.url) as conn:
        rows = conn.execute(
            b"SELECT match_id, source_row_number FROM matches ORDER BY match_id"
        ).fetchall()
    assert rows == [(n, n) for n in range(1, 16)]


def test_staging_derivations_match_expected(transformed_db: TransformedDb) -> None:
    """Season, per-side result, points and total goals equal the hand-calculated values."""
    with psycopg.connect(transformed_db.url) as conn, conn.cursor(row_factory=dict_row) as cur:
        actual = cur.execute(
            b"SELECT m.source_row_number, s.season, s.season_start_year, s.home_result, "
            b"s.away_result, s.home_points, s.away_points, s.total_goals "
            b"FROM analytics_staging.stg_matches s JOIN matches m ON m.match_id = s.match_id "
            b"ORDER BY m.source_row_number"
        ).fetchall()
    assert [dict(r) for r in actual] == _expected("stg_matches")


def test_season_label_rule(transformed_db: TransformedDb) -> None:
    """July starts a season; ``1999/00`` wraps the century (FR-003). Rolled back afterwards."""
    dates = [
        "2000-01-01",
        "2000-06-30",
        "2000-07-01",
        "2009-12-31",
        "2010-01-01",
        "2019-06-30",
        "2019-07-01",
    ]
    expected = [
        ("1999/00", 1999),
        ("1999/00", 1999),
        ("2000/01", 2000),
        ("2009/10", 2009),
        ("2009/10", 2009),
        ("2018/19", 2018),
        ("2019/20", 2019),
    ]
    with psycopg.connect(transformed_db.url) as conn:
        actual: list[tuple[object, ...]] = []
        try:
            with conn.transaction():
                for number, date in enumerate(dates, start=1001):
                    conn.execute(
                        b"INSERT INTO matches (league_code, match_date, home_team_id, "
                        b"away_team_id, source_sha256, source_row_number, ft_home_goals, "
                        b"ft_away_goals, ft_result) VALUES ('E0', %s, "
                        b"(SELECT team_id FROM teams WHERE team_key = 'arsenal'), "
                        b"(SELECT team_id FROM teams WHERE team_key = 'chelsea'), "
                        b"repeat('0', 64), %s, 2, 1, 'H')",
                        (date, number),
                    )
                actual = [
                    tuple(r)
                    for r in conn.execute(
                        b"SELECT s.season, s.season_start_year "
                        b"FROM analytics_staging.stg_matches s "
                        b"JOIN matches m ON m.match_id = s.match_id "
                        b"WHERE m.source_row_number >= 1001 ORDER BY m.source_row_number"
                    ).fetchall()
                ]
                raise _Rollback
        except _Rollback:
            pass
        assert actual == expected
        assert conn.execute(b"SELECT count(*) FROM matches").fetchone() == (15,)
