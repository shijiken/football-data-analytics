import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from jsonschema import Draft202012Validator

from football_stats import pipeline
from football_stats.config import Settings
from football_stats.errors import VerificationFailedError
from football_stats.verify import run_verify
from tests.conftest import fixture_path
from tests.integration.helpers import REPO_ROOT

SCHEMA = json.loads(
    (REPO_ROOT / "specs/001-match-data-ingestion/contracts/verify-report.schema.json").read_text(
        "utf-8"
    )
)


def source_rows(name: str) -> list[dict[str, str]]:
    """Fixture data records read with the stdlib csv module (blank line -> empty dict)."""
    with fixture_path(name).open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        return [dict(zip(header, fields, strict=True)) if fields else {} for fields in reader]


def oracle_key(name: str) -> str:
    """Independent re-implementation of the team key (no import from the package)."""
    return " ".join(name.split()).casefold()


@pytest.mark.parametrize("name", ["matches_edge_cases.csv", "matches_real_sample.csv"])
def test_every_loaded_match_equals_source(
    name: str, settings: Settings, db_conn: psycopg.Connection
) -> None:
    pipeline.run(settings, source_file=fixture_path(name))
    excluded = {r[0] for r in db_conn.execute("SELECT source_row_number FROM excluded_rows")}
    db_rows = {
        r[0]: r[1:]
        for r in db_conn.execute(
            "SELECT m.source_row_number, m.match_date, h.team_key, a.team_key, "
            "m.ft_home_goals, m.ft_away_goals FROM matches m "
            "JOIN teams h ON h.team_id = m.home_team_id JOIN teams a ON a.team_id = m.away_team_id"
        )
    }
    records = source_rows(name)
    loaded = [n for n in range(1, len(records) + 1) if n not in excluded]
    assert sorted(db_rows) == loaded
    assert loaded
    for number in loaded:
        source = records[number - 1]
        match_date, home, away, home_goals, away_goals = db_rows[number]
        assert match_date.isoformat() == source["MatchDate"].strip()
        assert home == oracle_key(source["HomeTeam"])
        assert away == oracle_key(source["AwayTeam"])
        assert home_goals == int(float(source["FTHome"]))
        assert away_goals == int(float(source["FTAway"]))


def test_verify_command_reports_no_differences(settings: Settings) -> None:
    pipeline.run(settings, source_file=fixture_path("matches_valid.csv"))
    report = run_verify(settings, sample=0, seed=42)
    assert report.checked == 12
    assert report.differences == []


def test_verify_detects_tampered_row(settings: Settings, db_conn: psycopg.Connection) -> None:
    pipeline.run(settings, source_file=fixture_path("matches_valid.csv"))
    db_conn.execute(
        "UPDATE matches SET ft_home_goals = ft_home_goals + 1 WHERE source_row_number = 3"
    )
    db_conn.commit()
    with pytest.raises(VerificationFailedError) as info:
        run_verify(settings, sample=0, seed=42)
    assert info.value.checked == 12
    assert info.value.differences == [
        {"source_row_number": 3, "field": "ft_home_goals", "source": "1", "database": "2"}
    ]


def test_verify_reports_missing_database_row(
    settings: Settings, db_conn: psycopg.Connection
) -> None:
    pipeline.run(settings, source_file=fixture_path("matches_valid.csv"))
    db_conn.execute("DELETE FROM matches WHERE source_row_number = 5")
    db_conn.commit()
    with pytest.raises(VerificationFailedError) as info:
        run_verify(settings, sample=0, seed=42)
    assert [d["field"] for d in info.value.differences] == ["row"]
    assert info.value.differences[0]["source_row_number"] == 5


def test_verify_sample_is_seeded(settings: Settings) -> None:
    pipeline.run(settings, source_file=fixture_path("matches_valid.csv"))
    first = run_verify(settings, sample=5, seed=7)
    again = run_verify(settings, sample=5, seed=7)
    assert first.checked == 5
    assert first.checked_row_numbers == again.checked_row_numbers
    others = {run_verify(settings, sample=5, seed=s).checked_row_numbers for s in (1, 2, 3, 4)}
    assert len(others | {first.checked_row_numbers}) > 1


def _verify_cli(db_url: str, raw_dir: Path) -> tuple[int, dict[str, object]]:
    proc = subprocess.run(
        [sys.executable, "-m", "football_stats.cli", "verify", "--sample", "0"],
        env={
            "PATH": os.environ["PATH"],
            "PYTHONPATH": str(REPO_ROOT / "src"),
            "DATABASE_URL": db_url,
            "RAW_DATA_DIR": str(raw_dir),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    report = json.loads(proc.stdout)
    Draft202012Validator(SCHEMA).validate(report)
    return proc.returncode, report


def test_verify_cli_report_matches_schema(
    settings: Settings, db_conn: psycopg.Connection, tmp_path: Path
) -> None:
    pipeline.run(settings, source_file=fixture_path("matches_valid.csv"))
    code, report = _verify_cli(settings.database_url, settings.raw_data_dir)
    assert code == 0
    assert report == {"status": "succeeded", "checked": 12, "differences": []}

    db_conn.execute(
        "UPDATE matches SET ft_away_goals = ft_away_goals + 1 WHERE source_row_number = 2"
    )
    db_conn.commit()
    code, report = _verify_cli(settings.database_url, settings.raw_data_dir)
    assert code == 6
    assert report["error"] == "verification_failed"
    assert report["checked"] == 12
    assert report["differences"] == [
        {"source_row_number": 2, "field": "ft_away_goals", "source": "0", "database": "1"}
    ]
