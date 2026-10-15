"""Pipeline-level data contract tests against a real PostgreSQL (US1, US2 proofs)."""

import csv
import datetime as dt
import json
import logging
from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest

from football_stats import pipeline
from football_stats.config import Settings
from football_stats.data_contract import CONTRACT_VERSION, CURRENT_CONTRACT
from tests.conftest import fixture_path
from tests.contract_cases import FULL_ROW
from tests.integration import helpers
from tests.stages import CONTRACT_STARTED_AT, RUN_DATE

#: Per-rule counts of ``matches_contract.csv`` (plan.md "Derived expectations").
CONTRACT_FIXTURE_BY_RULE = {
    "ht_le_ft_home": 2,
    "ht_le_ft_away": 1,
    "ft_result_consistent": 3,
    "ht_result_consistent": 1,
    "sot_le_shots_home": 2,
    "sot_le_shots_away": 2,
    "odds_min": 2,
    "max_odds_ge_avg": 1,
    "date_in_range": 2,
}


def test_contract_fixture_loads_only_consistent_rows(
    settings: Settings, db_conn: psycopg.Connection
) -> None:
    report = pipeline.run(
        settings, source_file=fixture_path("matches_contract.csv"), started_at=CONTRACT_STARTED_AT
    )
    loaded = [r[0] for r in db_conn.execute("SELECT source_row_number FROM matches ORDER BY 1")]
    assert loaded == [1, 14, 15, 16, 18]

    expected = json.loads(fixture_path("matches_contract.expected.json").read_text("utf-8"))
    excluded = {
        n: {"reason": reason, "detail": detail}
        for n, reason, detail in db_conn.execute(
            "SELECT source_row_number, reason, detail FROM excluded_rows"
        )
    }
    assert excluded == {int(k): v for k, v in expected.items() if v != "loaded"}
    assert report.rows_read == 23 == report.rows_loaded + report.rows_excluded
    assert (report.rows_loaded, report.rows_excluded) == (5, 18)

    assert [r[0] for r in db_conn.execute("SELECT league_code FROM leagues")] == ["E0"]
    teams = db_conn.execute("SELECT team_key, name FROM teams ORDER BY team_key").fetchall()
    assert teams == [
        ("arsenal", "Arsenal"),
        ("chelsea", "Chelsea"),
        ("leeds", "leeds"),
        ("wolves", "WOLVES"),
    ]


def test_run_report_and_record_contract_metadata(
    settings: Settings, db_conn: psycopg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="football_stats")
    report = pipeline.run(
        settings, source_file=fixture_path("matches_contract.csv"), started_at=CONTRACT_STARTED_AT
    )
    assert report.contract_version == CONTRACT_VERSION == 1
    assert report.violations_by_rule == CONTRACT_FIXTURE_BY_RULE
    assert list(report.violations_by_rule) == list(CURRENT_CONTRACT.rule_ids)
    assert report.excluded_by_reason["contract_violation"] == 15
    assert report.matches_removed == report.excluded_rows_removed == 0

    row = db_conn.execute(
        "SELECT contract_version, violations_by_rule, excluded_by_reason, matches_removed, "
        "excluded_rows_removed FROM ingest_runs"
    ).fetchall()
    assert row == [
        (
            report.contract_version,
            report.violations_by_rule,
            report.excluded_by_reason,
            report.matches_removed,
            report.excluded_rows_removed,
        )
    ]

    lines = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("contract rule=")
    ]
    assert lines == [
        f"contract rule={rule_id} violations={CONTRACT_FIXTURE_BY_RULE[rule_id]}"
        for rule_id in CURRENT_CONTRACT.rule_ids
    ]


def test_date_in_range_boundary_uses_run_start_date(
    settings: Settings, db_conn: psycopg.Connection, tmp_path: Path
) -> None:
    source = tmp_path / "boundary.csv"
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FULL_ROW), lineterminator="\n")
        writer.writeheader()
        writer.writerow({**FULL_ROW, "MatchDate": "2021-06-30"})
        writer.writerow({**FULL_ROW, "MatchDate": "2021-07-01", "HomeTeam": "Everton"})
    # 2021-07-01 01:00 at UTC+2 is 2021-06-30 23:00 UTC: the run date is 2021-06-30.
    started_at = dt.datetime(2021, 7, 1, 1, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    pipeline.run(settings, source_file=source, started_at=started_at)

    assert db_conn.execute("SELECT source_row_number FROM matches").fetchall() == [(1,)]
    assert db_conn.execute(
        "SELECT source_row_number, reason, detail FROM excluded_rows"
    ).fetchall() == [(2, "contract_violation", "date_in_range")]


def _validate_all_in_rolled_back_transaction(db_url: str) -> None:
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


def test_no_loaded_match_violates_contract(settings: Settings, db_url: str) -> None:
    for name in ("matches_contract.csv", "matches_real_sample.csv"):
        pipeline.run(settings, source_file=fixture_path(name), started_at=CONTRACT_STARTED_AT)
    _validate_all_in_rolled_back_transaction(db_url)


def test_upsert_rewrites_existing_violating_match(
    empty_db_url: str, make_settings: Callable[[str], Settings]
) -> None:
    source = fixture_path("matches_contract.csv")
    helpers.seed_pre_contract(empty_db_url, source, RUN_DATE)
    helpers.upgrade(empty_db_url)
    pipeline.run(make_settings(empty_db_url), source_file=source, started_at=CONTRACT_STARTED_AT)
    with psycopg.connect(empty_db_url) as conn:
        row = conn.execute(
            "SELECT m.ft_result, m.source_row_number FROM matches m "
            "JOIN teams h ON h.team_id = m.home_team_id JOIN teams a ON a.team_id = m.away_team_id "
            "WHERE m.league_code = 'E0' AND m.match_date = '2020-11-01' "
            "AND h.team_key = 'leeds' AND a.team_key = 'wolves'"
        ).fetchone()
    assert row == ("H", 18)
