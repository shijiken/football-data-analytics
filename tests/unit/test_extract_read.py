from pathlib import Path

import polars as pl
import pytest
from football_stats.extract import check_structure, read_header, read_source

from football_stats.errors import MalformedSourceError
from football_stats.source_contract import EXPECTED_COLUMNS
from tests.conftest import fixture_path


def test_read_header_returns_raw_names(tmp_path: Path) -> None:
    path = tmp_path / "dup.csv"
    path.write_text("a,b,a\n1,2,3\n", encoding="utf-8")
    assert read_header(path) == ["a", "b", "a"]
    assert read_header(fixture_path("matches_valid.csv")) == list(EXPECTED_COLUMNS)


def test_read_source_all_strings_with_row_numbers() -> None:
    frame = read_source(fixture_path("matches_valid.csv"))
    assert frame.columns == [*EXPECTED_COLUMNS, "source_row_number"]
    for name in EXPECTED_COLUMNS:
        assert frame.schema[name] == pl.String
    assert frame.schema["source_row_number"] == pl.UInt32
    assert frame["source_row_number"].to_list() == list(range(1, 13))
    assert frame["FTHome"][0] == "2.0"


def test_read_source_counts_blank_line_as_record() -> None:
    frame = read_source(fixture_path("matches_edge_cases.csv"))
    assert frame.height == 30
    first = frame.row(0, named=True)
    assert first.pop("source_row_number") == 1
    assert all(value is None for value in first.values())


def test_read_source_preserves_whitespace() -> None:
    frame = read_source(fixture_path("matches_edge_cases.csv"))
    assert frame["HomeTeam"][17] == "Ajax "
    assert frame["HomeTeam"][5] == "   "


def test_read_source_header_only_gives_empty_frame() -> None:
    frame = read_source(fixture_path("matches_header_only.csv"))
    assert frame.height == 0
    assert frame.columns == [*EXPECTED_COLUMNS, "source_row_number"]


@pytest.mark.parametrize(
    "name", ["matches_valid.csv", "matches_real_sample.csv", "matches_edge_cases.csv"]
)
def test_check_structure_accepts_valid_and_blank_lines(name: str) -> None:
    check_structure(fixture_path(name), 48)


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("matches_ragged_short.csv", "record 2 has 47 fields, expected 48"),
        ("matches_ragged_long.csv", "record 2 has 49 fields, expected 48"),
        ("matches_invalid_utf8.csv", "UTF-8"),
    ],
)
def test_malformed_source_raises(name: str, message: str) -> None:
    """``check_structure`` is the guard: polars alone would pad the 47-field row."""
    with pytest.raises(MalformedSourceError, match=message):
        check_structure(fixture_path(name), 48)


def test_read_source_malformed_raises() -> None:
    """Defence in depth: polars itself rejects a row with too many fields."""
    with pytest.raises(MalformedSourceError):
        read_source(fixture_path("matches_ragged_long.csv"))


def test_empty_file_is_malformed(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_bytes(b"")
    with pytest.raises(MalformedSourceError):
        read_header(path)
    with pytest.raises(MalformedSourceError):
        read_source(path)
