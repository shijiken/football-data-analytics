from pathlib import Path

import psycopg
import pytest

from football_stats import pipeline
from football_stats.config import Settings
from tests.conftest import fixture_path
from tests.integration import helpers


@pytest.mark.parametrize(
    "name", ["matches_valid.csv", "matches_edge_cases.csv", "matches_real_sample.csv"]
)
def test_second_run_leaves_identical_state(
    name: str, settings: Settings, db_conn: psycopg.Connection
) -> None:
    source = fixture_path(name)
    pipeline.run(settings, source_file=source)
    first = helpers.snapshot(db_conn)
    db_conn.commit()
    pipeline.run(settings, source_file=source)
    second = helpers.snapshot(db_conn)

    for table in ("leagues", "teams", "matches", "excluded_rows"):
        assert second[table] == first[table], table
    assert len(second["ingest_runs"]) == 2
    # Same source and the same read/loaded/excluded counts in both runs; only run_id and the
    # inserted/updated/unchanged split differ (the second run changes nothing).
    run_one, run_two = second["ingest_runs"]
    assert run_one[1:9] == run_two[1:9]
    assert run_two[9:] == (0, 0, run_two[6])
    assert second["ingest_runs"][0] == first["ingest_runs"][0]


def test_second_run_reports_all_unchanged(settings: Settings) -> None:
    source = fixture_path("matches_edge_cases.csv")
    first = pipeline.run(settings, source_file=source)
    second = pipeline.run(settings, source_file=source)
    assert first.matches_inserted == first.rows_loaded
    assert second.matches_inserted == 0
    assert second.matches_updated == 0
    assert second.matches_unchanged == second.rows_loaded == first.rows_loaded
    assert second.excluded_by_reason == first.excluded_by_reason


def test_changed_source_updates_match_in_place(
    settings: Settings, db_conn: psycopg.Connection, tmp_path: Path
) -> None:
    original = fixture_path("matches_valid.csv")
    first = pipeline.run(settings, source_file=original)
    ids_before = db_conn.execute(
        "SELECT source_row_number, match_id FROM matches ORDER BY 1"
    ).fetchall()
    db_conn.commit()

    lines = original.read_text("utf-8").splitlines()
    fields = lines[1].split(",")
    assert fields[11:14] == ["2.0", "1.0", "H"]
    fields[11:14] = ["0.0", "3.0", "A"]
    lines[1] = ",".join(fields)
    changed = tmp_path / "changed.csv"
    changed.write_text("\n".join(lines) + "\n", encoding="utf-8")

    second = pipeline.run(settings, source_file=changed)
    assert second.source_sha256 != first.source_sha256
    assert (second.matches_inserted, second.matches_updated, second.matches_unchanged) == (0, 12, 0)

    assert db_conn.execute("SELECT count(*) FROM matches").fetchone() == (12,)
    assert db_conn.execute(
        "SELECT source_row_number, match_id FROM matches ORDER BY 1"
    ).fetchall() == (ids_before)
    edited = db_conn.execute(
        "SELECT ft_home_goals, ft_away_goals, ft_result FROM matches WHERE source_row_number = 1"
    ).fetchone()
    assert edited == (0, 3, "A")
    shas = db_conn.execute("SELECT DISTINCT source_sha256 FROM matches").fetchall()
    assert shas == [(second.source_sha256,)]
