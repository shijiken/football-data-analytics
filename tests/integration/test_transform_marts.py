"""Staging and mart contents against hand-calculated expectations (feature 003, FR-009)."""

from __future__ import annotations

from decimal import Decimal

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


def _mart(transformed_db: TransformedDb, name: str) -> list[dict[str, object]]:
    with psycopg.connect(transformed_db.url) as conn:
        return helpers.mart_rows(conn, name)


def _form_row(rows: list[dict[str, object]], team: str, source_row: int) -> dict[str, object]:
    (row,) = [r for r in rows if r["team_key"] == team and r["source_row_number"] == source_row]
    return row


def test_team_form_matches_expected(transformed_db: TransformedDb) -> None:
    """Every team-form row equals the hand-calculated value (SC-002)."""
    assert _mart(transformed_db, "mart_team_form") == _expected("mart_team_form")


def test_home_advantage_matches_expected(transformed_db: TransformedDb) -> None:
    """Every home-advantage row equals the hand-calculated value (SC-002)."""
    assert _mart(transformed_db, "mart_league_season_home_advantage") == _expected(
        "mart_league_season_home_advantage"
    )


def test_goals_per_season_matches_expected(transformed_db: TransformedDb) -> None:
    """Every goals row equals the hand-calculated value (SC-002)."""
    assert _mart(transformed_db, "mart_league_season_goals") == _expected(
        "mart_league_season_goals"
    )


def test_team_ratings_matches_expected(transformed_db: TransformedDb) -> None:
    """Every rating row equals the hand-calculated value (SC-002)."""
    assert _mart(transformed_db, "mart_team_ratings") == _expected("mart_team_ratings")


def test_first_match_has_empty_form(transformed_db: TransformedDb) -> None:
    """A team's first match has form_matches 0 and null form sums (US2-AS1)."""
    rows = _mart(transformed_db, "mart_team_form")
    firsts = [r for r in rows if r["team_match_number"] == 1]
    assert len(firsts) == len({r["team_key"] for r in rows}) == 9
    for row in firsts:
        assert row["form_matches"] == 0
        assert row["form_points"] is None
        assert row["form_goals_for"] is None
        assert row["form_goals_against"] is None


def test_same_date_matches_ordered_by_match_id(transformed_db: TransformedDb) -> None:
    """Two matches of one team on one date are ordered by match_id (FR-005)."""
    rows = _mart(transformed_db, "mart_team_form")
    fourth = _form_row(rows, "arsenal", 4)
    fifth = _form_row(rows, "arsenal", 5)
    assert fourth["match_date"] == fifth["match_date"]
    assert (fourth["team_match_number"], fifth["team_match_number"]) == (4, 5)
    assert fifth["form_points"] == 8  # includes match 4 (a draw), unlike match 4 itself (7)


def test_form_window_capped_at_five_previous(transformed_db: TransformedDb) -> None:
    """The window holds at most the 5 previous matches (FR-005)."""
    rows = _mart(transformed_db, "mart_team_form")
    assert max(int(str(r["form_matches"])) for r in rows) == 5
    ninth = _form_row(rows, "arsenal", 9)
    assert (ninth["form_points"], ninth["form_goals_for"], ninth["form_goals_against"]) == (7, 8, 6)


def test_shares_sum_to_one_within_rounding(transformed_db: TransformedDb) -> None:
    """The three shares sum to 1 within the rounding tolerance (US2-AS2)."""
    rows = _mart(transformed_db, "mart_league_season_home_advantage")
    sums: dict[tuple[object, object], Decimal] = {}
    for row in rows:
        total = sum(
            (Decimal(str(row[c])) for c in ("home_win_share", "draw_share", "away_win_share")),
            Decimal(0),
        )
        sums[(row["league_code"], row["season"])] = total
        assert abs(total - 1) <= Decimal("0.00015")
    assert sums[("SP1", "2021/22")] == Decimal("0.9999")


def test_zero_count_shares_are_zero_not_null(transformed_db: TransformedDb) -> None:
    """A league-season without draws or away wins has shares of 0.0000, not null."""
    rows = _mart(transformed_db, "mart_league_season_home_advantage")
    (arg,) = [r for r in rows if (r["league_code"], r["season"]) == ("ARG", "2021/22")]
    assert arg["draw_share"] == "0.0000"
    assert arg["away_win_share"] == "0.0000"


def test_league_totals_equal_staged_goals(transformed_db: TransformedDb) -> None:
    """League-season totals equal the sums over staging (US2-AS3)."""
    with psycopg.connect(transformed_db.url) as conn:
        staged = conn.execute(
            b"SELECT league_code, season, sum(home_goals + away_goals), count(*) "
            b"FROM analytics_staging.stg_matches GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall()
        marted = conn.execute(
            b"SELECT league_code, season, total_goals, matches_played "
            b"FROM analytics_marts.mart_league_season_goals ORDER BY 1, 2"
        ).fetchall()
    assert staged
    assert marted == staged


def test_unrated_team_has_no_rating_values(transformed_db: TransformedDb) -> None:
    """A team without any Elo has a rating row of nulls but still has team-form rows."""
    ratings = _mart(transformed_db, "mart_team_ratings")
    (river,) = [r for r in ratings if r["team_key"] == "river plate"]
    assert river["rated_matches"] == 0
    values = [v for k, v in river.items() if k not in {"team_key", "team_name", "rated_matches"}]
    assert len(values) == 6
    assert all(v is None for v in values)
    forms = _mart(transformed_db, "mart_team_form")
    assert len([r for r in forms if r["team_key"] == "river plate"]) == 3


def test_excluded_rows_never_reach_marts(transformed_db: TransformedDb) -> None:
    """The row excluded by ingestion (row 16) appears in no mart."""
    forms = _mart(transformed_db, "mart_team_form")
    assert not [r for r in forms if r["match_date"] == "2021-09-25"]
    home = _mart(transformed_db, "mart_league_season_home_advantage")
    (e0,) = [r for r in home if (r["league_code"], r["season"]) == ("E0", "2021/22")]
    assert e0["matches_played"] == 7
    ratings = _mart(transformed_db, "mart_team_ratings")
    elos = [r[c] for r in ratings for c in ("latest_elo", "peak_elo", "lowest_elo")]
    assert 1999.0 not in elos
