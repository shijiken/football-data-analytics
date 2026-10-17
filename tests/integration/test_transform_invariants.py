"""Every singular dbt test is shown to detect its violation (feature 003, FR-006, R5, R10).

Each case corrupts the shared session database inside a transaction, runs the compiled SQL of
one singular test in that transaction (it must return rows), then rolls back (it returns none).
"""

from __future__ import annotations

import psycopg
import pytest

from tests.integration.conftest import TransformedDb
from tests.integration.dbt_runner import TRANSFORM_DIR

_MARTS = "analytics_marts"

#: (case id, singular test name, corrupting SQL) - data-model.md §4.
MUTATIONS: tuple[tuple[str, str, str], ...] = (
    (
        "team_form_row_missing",
        "assert_team_form_two_rows_per_match",
        f"DELETE FROM {_MARTS}.mart_team_form WHERE (match_id, team_id) IN "
        "(SELECT match_id, team_id FROM "
        f"{_MARTS}.mart_team_form ORDER BY match_id, team_id LIMIT 1)",
    ),
    (
        "team_form_goals_swapped",
        "assert_team_form_two_rows_per_match",
        f"UPDATE {_MARTS}.mart_team_form SET goals_for = goals_against, goals_against = goals_for "
        "WHERE goals_for <> goals_against",
    ),
    (
        "form_includes_current_match",
        "assert_team_form_matches_lag_recomputation",
        f"UPDATE {_MARTS}.mart_team_form SET form_points = form_points + points "
        "WHERE form_matches > 0 AND points > 0",
    ),
    (
        "shares_do_not_sum_to_one",
        "assert_home_advantage_shares_sum_to_one",
        f"UPDATE {_MARTS}.mart_league_season_home_advantage SET draw_share = draw_share + 0.01",
    ),
    (
        "home_advantage_count_off",
        "assert_home_advantage_reconciles_with_staging",
        f"UPDATE {_MARTS}.mart_league_season_home_advantage "
        "SET matches_played = matches_played + 1 "
        "WHERE (league_code, season) = (SELECT league_code, season FROM "
        f"{_MARTS}.mart_league_season_home_advantage ORDER BY 1, 2 LIMIT 1)",
    ),
    (
        "home_away_shares_swapped",
        "assert_home_advantage_reconciles_with_staging",
        f"UPDATE {_MARTS}.mart_league_season_home_advantage "
        "SET home_win_share = away_win_share, away_win_share = home_win_share "
        "WHERE home_win_share <> away_win_share",
    ),
    (
        "goals_total_off",
        "assert_goals_reconcile_with_staging",
        f"UPDATE {_MARTS}.mart_league_season_goals SET total_goals = total_goals + 1",
    ),
    (
        "btts_share_off",
        "assert_goals_reconcile_with_staging",
        f"UPDATE {_MARTS}.mart_league_season_goals SET btts_share = btts_share + 0.0001",
    ),
    (
        "peak_date_not_earliest",
        "assert_team_ratings_reconcile_with_staging",
        f"UPDATE {_MARTS}.mart_team_ratings SET peak_elo_date = latest_elo_date "
        "WHERE team_key = 'arsenal'",
    ),
)


def _violations(conn: psycopg.Connection, compiled: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM (\n{compiled}\n) t".encode()).fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.parametrize(("case_id", "test_name", "sql"), MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_singular_test_detects_violation(
    transformed_db: TransformedDb, case_id: str, test_name: str, sql: str
) -> None:
    """The singular test returns rows after the corruption and none after the rollback."""
    compiled = transformed_db.build.compiled_sql(test_name)
    with psycopg.connect(transformed_db.url) as conn:
        assert _violations(conn, compiled) == 0, "the test must pass before the corruption"
        conn.rollback()
        conn.execute(sql.encode())
        assert _violations(conn, compiled) >= 1, case_id
        conn.rollback()
        assert _violations(conn, compiled) == 0, "the rollback must restore the data"


def test_singular_tests_cover_every_file() -> None:
    """Every singular test file has at least one mutation case, and no case is orphaned."""
    files = {p.stem for p in (TRANSFORM_DIR / "tests").glob("*.sql")}
    assert {name for _, name, _ in MUTATIONS} == files


def test_singular_tests_pass_on_fixture(transformed_db: TransformedDb) -> None:
    """All singular tests passed in the session build."""
    statuses = transformed_db.build.statuses()
    names = {p.stem for p in (TRANSFORM_DIR / "tests").glob("*.sql")}
    assert names
    for name in names:
        assert statuses[f"test.football_analytics.{name}"] == "pass", name
