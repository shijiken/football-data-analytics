import csv
import json
import re

import psycopg
import pytest

from football_stats import pipeline
from football_stats.config import Settings
from tests.conftest import fixture_path
from tests.stages import CONTRACT_STARTED_AT


def source_records(name: str) -> list[dict[str, str | None]]:
    """Re-read a fixture with the stdlib csv module: untrimmed strings, empty -> None."""
    with fixture_path(name).open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        return [
            {column: (value or None) for column, value in zip(header, fields, strict=True)}
            if fields
            else dict.fromkeys(header)
            for fields in reader
        ]


def test_each_reason_persisted_with_raw_record(
    settings: Settings, db_conn: psycopg.Connection
) -> None:
    expected = json.loads(fixture_path("matches_edge_cases.expected.json").read_text("utf-8"))
    excluded = {int(n): v for n, v in expected.items() if v != "loaded"}
    records = source_records("matches_edge_cases.csv")
    pipeline.run(settings, source_file=fixture_path("matches_edge_cases.csv"))

    rows = db_conn.execute(
        "SELECT source_row_number, reason, detail, raw_record FROM excluded_rows "
        "ORDER BY source_row_number"
    ).fetchall()
    assert [r[0] for r in rows] == sorted(excluded)
    for number, reason, detail, raw_record in rows:
        assert {"reason": reason, "detail": detail} == excluded[number]
        assert raw_record == records[number - 1]
    blank_line = rows[0][3]
    assert len(blank_line) == 48
    assert all(value is None for value in blank_line.values())


def test_excluded_rows_link_to_run(settings: Settings, db_conn: psycopg.Connection) -> None:
    report = pipeline.run(settings, source_file=fixture_path("matches_edge_cases.csv"))
    rows = db_conn.execute("SELECT DISTINCT run_id, source_sha256 FROM excluded_rows").fetchall()
    assert rows == [(report.run_id, report.source_sha256)]


def test_duplicate_detail_points_to_kept_row(
    settings: Settings, db_conn: psycopg.Connection
) -> None:
    pipeline.run(settings, source_file=fixture_path("matches_edge_cases.csv"))
    details = [
        r[0] for r in db_conn.execute("SELECT detail FROM excluded_rows WHERE reason = 'duplicate'")
    ]
    assert len(details) == 2
    kept = {r[0] for r in db_conn.execute("SELECT source_row_number FROM matches")}
    for detail in details:
        match = re.fullmatch(r"duplicate of source row ([0-9]+)", detail)
        assert match is not None
        assert int(match.group(1)) in kept


@pytest.mark.parametrize(
    "name", ["matches_edge_cases.csv", "matches_real_sample.csv", "matches_contract.csv"]
)
def test_db_reconciles_with_run_counts(
    name: str, settings: Settings, db_conn: psycopg.Connection
) -> None:
    report = pipeline.run(settings, source_file=fixture_path(name), started_at=CONTRACT_STARTED_AT)
    loaded = db_conn.execute(
        "SELECT count(*) FROM matches WHERE source_sha256 = %s", (report.source_sha256,)
    ).fetchone()
    excluded = db_conn.execute(
        "SELECT count(*) FROM excluded_rows WHERE source_sha256 = %s", (report.source_sha256,)
    ).fetchone()
    assert loaded is not None
    assert excluded is not None
    assert loaded[0] + excluded[0] == report.rows_read
    assert (loaded[0], excluded[0]) == (report.rows_loaded, report.rows_excluded)
