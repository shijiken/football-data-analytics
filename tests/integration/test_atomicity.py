import dataclasses
import datetime as dt

import polars as pl
import psycopg
import pytest

from football_stats import clean, load, pipeline
from football_stats.config import Settings
from football_stats.data_contract import CURRENT_CONTRACT
from football_stats.errors import AccountingError, ClockSkewError, ConcurrentRunError, LoadError
from football_stats.source_contract import ExclusionReason
from tests.conftest import fixture_path
from tests.integration import helpers
from tests.stages import RUN_DATE, partition_file

SHA = "c" * 64


def _broken_batch() -> tuple[clean.CleanBatch, pl.DataFrame]:
    """A batch whose leagues/teams are valid but whose first match has home key = away key."""
    part = partition_file(fixture_path("matches_valid.csv"))
    batch = clean.build_batch(part.accepted, SHA)
    matches = batch.matches.with_columns(
        pl.when(pl.col("source_row_number") == 1)
        .then(pl.col("home_team_key"))
        .otherwise(pl.col("away_team_key"))
        .alias("away_team_key")
    )
    leagues = pl.concat([batch.leagues, pl.DataFrame({"league_code": ["ZZ"]})])
    teams = pl.concat([batch.teams, pl.DataFrame({"team_key": ["newcomer"], "name": ["Newcomer"]})])
    return clean.CleanBatch(leagues=leagues, teams=teams, matches=matches), part.excluded


def _run_meta() -> load.RunMeta:
    return load.RunMeta(
        started_at=dt.datetime.now(dt.UTC),
        source_url="file:///x.csv",
        source_sha256=SHA,
        source_bytes=1,
        raw_path="/x.csv",
        rows_read=12,
        rows_excluded=0,
        excluded_by_reason={reason.value: 0 for reason in ExclusionReason},
        run_date=RUN_DATE,
        contract_version=1,
        violations_by_rule={rule_id: 0 for rule_id in CURRENT_CONTRACT.rule_ids},
    )


@pytest.mark.parametrize("preloaded", [False, True], ids=["empty", "preloaded"])
def test_db_failure_mid_load_rolls_back(
    preloaded: bool, settings: Settings, db_conn: psycopg.Connection
) -> None:
    if preloaded:
        pipeline.run(settings, source_file=fixture_path("matches_valid.csv"))
    before = helpers.snapshot(db_conn)
    db_conn.commit()

    batch, excluded = _broken_batch()
    run = _run_meta()
    with pytest.raises(LoadError):
        load.load_run(db_conn, batch, excluded, run)
    assert helpers.snapshot(db_conn) == before


@pytest.mark.parametrize("preloaded", [False, True], ids=["empty", "preloaded"])
def test_unresolvable_team_raises_accounting_error_and_rolls_back(
    preloaded: bool, settings: Settings, db_conn: psycopg.Connection
) -> None:
    """A staged match whose team is not in `teams` must not be dropped and counted unchanged."""
    if preloaded:
        pipeline.run(settings, source_file=fixture_path("matches_valid.csv"))
    before = helpers.snapshot(db_conn)
    db_conn.commit()

    part = partition_file(fixture_path("matches_valid.csv"))
    batch = clean.build_batch(part.accepted, SHA)
    ghost = batch.matches.with_columns(
        pl.col("home_team_key").replace("derby", "ghost"),
        pl.col("away_team_key").replace("derby", "ghost"),
    )
    broken = clean.CleanBatch(leagues=batch.leagues, teams=batch.teams, matches=ghost)
    with pytest.raises(AccountingError):
        load.load_run(db_conn, broken, part.excluded, _run_meta())
    assert helpers.snapshot(db_conn) == before


def test_concurrent_run_rejected(settings: Settings, db_conn: psycopg.Connection) -> None:
    before = helpers.snapshot(db_conn)
    db_conn.commit()
    with psycopg.connect(settings.database_url, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (load.LOCK_KEY,))
        with pytest.raises(ConcurrentRunError):
            pipeline.run(settings, source_file=fixture_path("matches_valid.csv"))
        other.execute("SELECT pg_advisory_unlock(%s)", (load.LOCK_KEY,))
    assert helpers.snapshot(db_conn) == before


def test_db_clock_behind_run_date_fails_before_writes(
    settings: Settings, db_conn: psycopg.Connection
) -> None:
    pipeline.run(settings, source_file=fixture_path("matches_valid.csv"))
    before = helpers.snapshot(db_conn)
    db_conn.commit()

    part = partition_file(fixture_path("matches_valid.csv"))
    batch = clean.build_batch(part.accepted, SHA)
    future = dt.date(2999, 1, 1)
    run = dataclasses.replace(_run_meta(), run_date=future)
    with pytest.raises(ClockSkewError) as info:
        load.load_run(db_conn, batch, part.excluded, run)
    assert future.isoformat() in str(info.value)
    db_date = db_conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()
    assert db_date is not None
    assert db_date[0].isoformat() in str(info.value)
    assert helpers.snapshot(db_conn) == before
