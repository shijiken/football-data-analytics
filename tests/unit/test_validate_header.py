import pytest
from football_stats.extract import read_header
from football_stats.validate import check_header

from football_stats.errors import MalformedSourceError, MissingColumnsError
from football_stats.source_contract import EXPECTED_COLUMNS
from tests.conftest import fixture_path


def test_check_header_accepts_real_header() -> None:
    assert check_header(list(EXPECTED_COLUMNS)) == []
    assert check_header(read_header(fixture_path("matches_real_sample.csv"))) == []


def test_check_header_names_all_missing_columns() -> None:
    header = read_header(fixture_path("matches_missing_columns.csv"))
    with pytest.raises(MissingColumnsError) as info:
        check_header(header)
    assert info.value.missing == ("AwayTeam", "FTHome")
    assert "AwayTeam, FTHome" in str(info.value)


def test_check_header_rejects_duplicate_names() -> None:
    header = read_header(fixture_path("matches_duplicate_header.csv"))
    with pytest.raises(MalformedSourceError, match="HomeTeam"):
        check_header(header)


def test_check_header_ignores_order_and_allows_extra() -> None:
    header = [*reversed(EXPECTED_COLUMNS), "Notes", "Source"]
    assert check_header(header) == ["Notes", "Source"]
