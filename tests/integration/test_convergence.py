"""Re-running ingestion on the same file converges the database to a fresh build (US3)."""

from collections.abc import Callable
from pathlib import Path

import polars as pl
import psycopg
import pytest

from football_stats import pipeline
from football_stats.config import Settings
from football_stats.data_contract import CURRENT_CONTRACT, ContractRule, DataContract, Term
from tests.conftest import fixture_path
from tests.integration import helpers
from tests.stages import CONTRACT_STARTED_AT, RUN_DATE

MakeSettings = Callable[[str], Settings]


def _upgrade_and_rerun(url: str, make_settings: MakeSettings, source: Path) -> pipeline.RunReport:
    """Seed a feature-001 database from ``source``, upgrade it, then re-run the pipeline."""
    helpers.seed_pre_contract(url, source, RUN_DATE)
    helpers.upgrade(url)
    return pipeline.run(make_settings(url), source_file=source, started_at=CONTRACT_STARTED_AT)


def _content(url: str) -> dict[str, list[object]]:
    with psycopg.connect(url) as conn:
        return helpers.content_snapshot(conn)


@pytest.mark.parametrize(
    "name", ["matches_contract.csv", "matches_edge_cases.csv", "matches_real_sample.csv"]
)
def test_upgrade_then_rerun_equals_fresh_build(
    name: str, empty_db_url: str, db_url: str, settings: Settings, make_settings: MakeSettings
) -> None:
    source = fixture_path(name)
    _upgrade_and_rerun(empty_db_url, make_settings, source)
    pipeline.run(settings, source_file=source, started_at=CONTRACT_STARTED_AT)

    assert _content(empty_db_url) == _content(db_url)
    helpers.assert_contract_validates_read_only(empty_db_url)


def test_rerun_removes_now_violating_matches(
    empty_db_url: str, make_settings: MakeSettings
) -> None:
    source = fixture_path("matches_contract.csv")
    helpers.seed_pre_contract(empty_db_url, source, RUN_DATE)
    helpers.upgrade(empty_db_url)  # Alembic reconfigures logging; capture only afterwards
    with helpers.capture_messages() as messages:
        report = pipeline.run(
            make_settings(empty_db_url), source_file=source, started_at=CONTRACT_STARTED_AT
        )
    assert (report.matches_inserted, report.matches_updated, report.matches_unchanged) == (0, 1, 4)
    assert report.matches_removed == 13

    removed = [*range(2, 14), 22]
    expected = {
        int(k): v
        for k, v in helpers.load_expected("matches_contract.expected.json").items()
        if int(k) in removed
    }
    with psycopg.connect(empty_db_url) as conn:
        loaded = {r[0] for r in conn.execute("SELECT source_row_number FROM matches")}
        assert loaded == {1, 14, 15, 16, 18}
        rows = conn.execute(
            "SELECT source_row_number, reason, detail FROM excluded_rows "
            "WHERE source_row_number = ANY(%s)",
            (removed,),
        ).fetchall()
        assert {n: {"reason": reason, "detail": detail} for n, reason, detail in rows} == expected
        stored = conn.execute(
            "SELECT matches_removed FROM ingest_runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        assert stored == (13,)
    assert (
        "converge matches_removed=13 excluded_rows_removed=1 teams_removed=1 leagues_removed=1"
        in messages
    )


def test_rerun_loads_rows_whose_exclusion_no_longer_applies(
    empty_db_url: str, make_settings: MakeSettings
) -> None:
    report = _upgrade_and_rerun(empty_db_url, make_settings, fixture_path("matches_contract.csv"))
    assert report.excluded_rows_removed == 1
    with psycopg.connect(empty_db_url) as conn:
        assert conn.execute(
            "SELECT count(*) FROM excluded_rows WHERE source_row_number = 18"
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM matches WHERE source_row_number = 18"
        ).fetchone() == (1,)


def test_reload_after_loosened_contract_equals_fresh_build(
    db_url: str, empty_db_url: str, make_settings: MakeSettings
) -> None:
    source = fixture_path("matches_valid.csv")
    stricter = DataContract(
        99,
        (
            *CURRENT_CONTRACT.rules,
            ContractRule(
                "test_no_draws",
                "test only: no draws",
                (Term(("ft_result",), lambda _d: pl.col("ft_result") != "D"),),
            ),
        ),
    )
    strict_report = pipeline.run(
        make_settings(db_url), source_file=source, contract=stricter, started_at=CONTRACT_STARTED_AT
    )
    draws = strict_report.excluded_by_reason["contract_violation"]
    assert draws > 0
    with psycopg.connect(db_url) as conn:
        assert conn.execute(
            "SELECT DISTINCT detail FROM excluded_rows WHERE reason = 'contract_violation'"
        ).fetchall() == [("test_no_draws",)]

    loose_report = pipeline.run(
        make_settings(db_url), source_file=source, started_at=CONTRACT_STARTED_AT
    )
    assert loose_report.excluded_rows_removed == draws

    helpers.upgrade(empty_db_url)
    pipeline.run(make_settings(empty_db_url), source_file=source, started_at=CONTRACT_STARTED_AT)
    assert _content(db_url) == _content(empty_db_url)


def test_orphan_team_and_league_removed(empty_db_url: str, make_settings: MakeSettings) -> None:
    source = fixture_path("matches_contract.csv")
    helpers.seed_pre_contract(empty_db_url, source, RUN_DATE)
    helpers.upgrade(empty_db_url)
    with psycopg.connect(empty_db_url) as conn:
        assert conn.execute(
            "SELECT count(*) FROM teams WHERE team_key = 'ghost fc'"
        ).fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM leagues WHERE league_code = 'X1'").fetchone() == (
            1,
        )
    pipeline.run(make_settings(empty_db_url), source_file=source, started_at=CONTRACT_STARTED_AT)
    with psycopg.connect(empty_db_url) as conn:
        assert conn.execute(
            "SELECT count(*) FROM teams WHERE team_key = 'ghost fc'"
        ).fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM leagues WHERE league_code = 'X1'").fetchone() == (
            0,
        )
        names: dict[str, str] = dict(
            conn.execute("SELECT team_key, name FROM teams WHERE team_key IN ('leeds', 'wolves')")
        )
    assert names == {"leeds": "leeds", "wolves": "WOLVES"}


def test_converged_rerun_is_noop(empty_db_url: str, make_settings: MakeSettings) -> None:
    source = fixture_path("matches_contract.csv")
    _upgrade_and_rerun(empty_db_url, make_settings, source)
    with psycopg.connect(empty_db_url) as conn:
        before = helpers.snapshot(conn)
    report = pipeline.run(
        make_settings(empty_db_url), source_file=source, started_at=CONTRACT_STARTED_AT
    )
    assert (
        report.matches_inserted,
        report.matches_updated,
        report.matches_removed,
        report.excluded_rows_removed,
    ) == (0, 0, 0, 0)
    with psycopg.connect(empty_db_url) as conn:
        after = helpers.snapshot(conn)
    for table in ("leagues", "teams", "matches", "excluded_rows"):
        assert after[table] == before[table], table


def test_convergence_scoped_to_source_file(empty_db_url: str, make_settings: MakeSettings) -> None:
    helpers.seed_pre_contract(empty_db_url, fixture_path("matches_contract.csv"), RUN_DATE)
    helpers.upgrade(empty_db_url)
    report = pipeline.run(
        make_settings(empty_db_url),
        source_file=fixture_path("matches_valid.csv"),
        started_at=CONTRACT_STARTED_AT,
    )
    assert report.matches_removed == 0
    with psycopg.connect(empty_db_url) as conn:
        kept = conn.execute(
            "SELECT count(*) FROM matches WHERE source_row_number BETWEEN 2 AND 13 "
            "AND source_sha256 <> %s",
            (report.source_sha256,),
        ).fetchone()
    assert kept == (12,)
