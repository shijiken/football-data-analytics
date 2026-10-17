import datetime as dt
import hashlib
import json
from pathlib import Path

import psycopg
import pytest

from football_stats import pipeline
from football_stats.config import Settings
from football_stats.errors import MissingColumnsError
from tests.conftest import fixture_path
from tests.integration import helpers


def run(settings: Settings, name: str) -> pipeline.RunReport:
    return pipeline.run(settings, source_file=fixture_path(name))


def test_ingest_loads_valid_fixture(settings: Settings, db_conn: psycopg.Connection) -> None:
    report = run(settings, "matches_valid.csv")
    assert (report.rows_read, report.rows_loaded, report.rows_excluded) == (12, 12, 0)
    assert (report.matches_inserted, report.matches_updated, report.matches_unchanged) == (12, 0, 0)

    assert [r[0] for r in db_conn.execute("SELECT league_code FROM leagues ORDER BY 1")] == [
        "E0",
        "E1",
    ]
    assert [r[0] for r in db_conn.execute("SELECT team_key FROM teams ORDER BY 1")] == [
        "arsenal",
        "chelsea",
        "derby",
        "everton",
        "leeds",
        "liverpool",
    ]
    linked = db_conn.execute(
        "SELECT count(*) FROM matches m JOIN leagues l USING (league_code) "
        "JOIN teams h ON h.team_id = m.home_team_id JOIN teams a ON a.team_id = m.away_team_id "
        "WHERE m.home_team_id <> m.away_team_id"
    ).fetchone()
    assert linked == (12,)


def test_query_by_league_and_date_range(settings: Settings, db_conn: psycopg.Connection) -> None:
    run(settings, "matches_valid.csv")
    rows = db_conn.execute(
        "SELECT m.match_date, h.name AS home, a.name AS away, m.ft_home_goals, m.ft_away_goals "
        "FROM matches m JOIN teams h ON h.team_id = m.home_team_id "
        "JOIN teams a ON a.team_id = m.away_team_id "
        "WHERE m.league_code = 'E0' AND m.match_date BETWEEN '2020-07-01' AND '2021-06-30' "
        "ORDER BY m.match_date, home"
    ).fetchall()
    assert rows == [
        (dt.date(2020, 9, 12), "Arsenal", "Chelsea", 2, 1),
        (dt.date(2020, 9, 13), "Liverpool", "Everton", 0, 0),
        (dt.date(2020, 9, 19), "Chelsea", "Liverpool", 1, 3),
        (dt.date(2020, 9, 20), "Everton", "Arsenal", 2, 2),
        (dt.date(2020, 9, 26), "Arsenal", "Liverpool", 3, 1),
        (dt.date(2020, 9, 27), "Everton", "Chelsea", 0, 1),
        (dt.date(2021, 5, 9), "Liverpool", "Arsenal", 2, 0),
    ]


def test_team_spelling_variants_single_team(
    settings: Settings, db_conn: psycopg.Connection
) -> None:
    run(settings, "matches_edge_cases.csv")
    rows = db_conn.execute(
        "SELECT team_key, name FROM teams WHERE team_key IN ('ajax', 'colon santa fe', 'man city') "
        "ORDER BY team_key"
    ).fetchall()
    assert rows == [
        ("ajax", "Ajax"),
        ("colon santa fe", "Colon Santa Fe"),
        ("man city", "Man City"),
    ]


def test_optional_nulls_stored_as_null(settings: Settings, db_conn: psycopg.Connection) -> None:
    run(settings, "matches_valid.csv")
    columns = "kickoff_time, ht_result, home_elo, home_shots, odd_home, cluster_phb"
    sparse = db_conn.execute(
        f"SELECT {columns} FROM matches WHERE source_row_number = 4".encode()
    ).fetchone()
    assert sparse == (None,) * 6
    full = db_conn.execute(
        f"SELECT {columns} FROM matches WHERE source_row_number = 1".encode()
    ).fetchone()
    assert full == (dt.time(15, 0), "H", 1850.5, 14, 1.65, 0.1)


def test_ingest_report_counts(settings: Settings, db_conn: psycopg.Connection) -> None:
    expected = json.loads(fixture_path("matches_edge_cases.expected.json").read_text("utf-8"))
    loaded = sum(1 for v in expected.values() if v == "loaded")
    by_reason = dict.fromkeys(
        [
            "missing_field",
            "invalid_date",
            "missing_result",
            "invalid_value",
            "same_team",
            "duplicate",
        ],
        0,
    )
    for value in expected.values():
        if value != "loaded":
            by_reason[value["reason"]] += 1

    report = run(settings, "matches_edge_cases.csv")
    assert report.rows_read == len(expected) == report.rows_loaded + report.rows_excluded
    assert report.rows_loaded == loaded
    assert report.excluded_by_reason == by_reason
    assert report.rows_excluded == sum(by_reason.values())

    assert db_conn.execute("SELECT count(*) FROM matches").fetchone() == (loaded,)
    run_row = db_conn.execute(
        "SELECT rows_read, rows_loaded, rows_excluded, excluded_by_reason FROM ingest_runs"
    ).fetchall()
    assert run_row == [(report.rows_read, loaded, report.rows_excluded, by_reason)]


def test_run_records_source_and_raw_path(
    settings: Settings, db_conn: psycopg.Connection, tmp_path: Path
) -> None:
    source = fixture_path("matches_valid.csv")
    report = pipeline.run(settings, source_file=source)
    data = source.read_bytes()
    row = db_conn.execute(
        "SELECT run_id, source_url, source_sha256, source_bytes, raw_path FROM ingest_runs"
    ).fetchone()
    assert row is not None
    run_id, url, sha, size, raw_path = row
    assert run_id == report.run_id
    assert url == source.resolve().as_uri()
    assert sha == hashlib.sha256(data).hexdigest() == report.source_sha256
    assert size == len(data)
    assert raw_path == report.raw_path
    assert Path(raw_path).is_file()
    assert tmp_path in Path(raw_path).parents


def test_missing_columns_stops_before_db(settings: Settings, db_conn: psycopg.Connection) -> None:
    run(settings, "matches_valid.csv")
    before = helpers.snapshot(db_conn)
    with pytest.raises(MissingColumnsError) as info:
        run(settings, "matches_missing_columns.csv")
    assert info.value.missing == ("AwayTeam", "FTHome")
    assert helpers.snapshot(db_conn) == before
    assert len(before["ingest_runs"]) == 1


def test_header_only_file_loads_nothing(settings: Settings, db_conn: psycopg.Connection) -> None:
    report = run(settings, "matches_header_only.csv")
    assert (report.rows_read, report.rows_loaded, report.rows_excluded) == (0, 0, 0)
    counts = helpers.table_counts(db_conn)
    assert counts["ingest_runs"] == 1
    assert counts["matches"] == counts["teams"] == counts["leagues"] == 0
