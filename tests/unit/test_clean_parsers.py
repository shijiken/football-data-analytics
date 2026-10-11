import datetime as dt

import polars as pl
import pytest
from football_stats.clean import parse_frame

from football_stats.source_contract import COLUMN_SPECS, EXPECTED_COLUMNS

DB_COLUMN = {spec.source_name: spec.db_column for spec in COLUMN_SPECS}
BASE = {
    "Division": "E0",
    "MatchDate": "2020-09-12",
    "HomeTeam": "Arsenal",
    "AwayTeam": "Chelsea",
    "FTHome": "1",
    "FTAway": "0",
    "FTResult": "H",
}


def parse_one(column: str, value: str | None) -> tuple[object, bool]:
    """Parse ``value`` in ``column`` of an otherwise valid row; return (value, invalid)."""
    record: dict[str, str | None] = {name: None for name in EXPECTED_COLUMNS}
    record.update(BASE)
    record[column] = value
    raw = pl.DataFrame(record, schema=dict.fromkeys(EXPECTED_COLUMNS, pl.String)).with_columns(
        pl.lit(1, dtype=pl.UInt32).alias("source_row_number")
    )
    parsed = parse_frame(raw)
    db = DB_COLUMN[column]
    return parsed[db][0], bool(parsed[f"{db}__invalid"][0])


@pytest.mark.parametrize("blank", [None, "", "   ", "\t"])
@pytest.mark.parametrize(
    "column", ["MatchTime", "HomeElo", "HomeShots", "HTResult", "OddHome", "C_LTH"]
)
def test_empty_is_null_not_invalid(column: str, blank: str | None) -> None:
    assert parse_one(column, blank) == (None, False)


def test_parse_date_valid() -> None:
    assert parse_one("MatchDate", "2021-02-28") == (dt.date(2021, 2, 28), False)
    assert parse_one("MatchDate", " 2020-02-29 ") == (dt.date(2020, 2, 29), False)


@pytest.mark.parametrize(
    "value",
    [
        "2021-02-30",
        "03/12/2020",
        "2021-13-01",
        "20210101",
        "2021-1-5",
        "\u0662\u0660\u0662\u0661-01-05",
        "0000-01-01",
    ],
)
def test_parse_date_invalid(value: str) -> None:
    assert parse_one("MatchDate", value) == (None, True)


def test_parse_date_blank_is_null_without_flag() -> None:
    assert parse_one("MatchDate", " ") == (None, False)


def test_parse_time() -> None:
    assert parse_one("MatchTime", "19:45:00") == (dt.time(19, 45), False)
    for bad in ["25:00:00", "19:45", "7pm", "19:60:00", "19:45:61"]:
        assert parse_one("MatchTime", bad) == (None, True)


def test_parse_count() -> None:
    assert parse_one("HomeShots", "3.0") == (3, False)
    assert parse_one("HomeShots", "0") == (0, False)
    assert parse_one("HomeShots", " 12.00 ") == (12, False)
    assert parse_one("HomeShots", "32767") == (32767, False)
    for bad in ["2.5", "-1", "40000", "32768", "nan", "1e2", "\u0663", "9" * 30, "3."]:
        assert parse_one("HomeShots", bad) == (None, True)


def test_parse_decimal() -> None:
    assert parse_one("OddHome", "1.65") == (1.65, False)
    assert parse_one("HandiSize", "-0.25") == (-0.25, False)
    assert parse_one("OddHome", "3") == (3.0, False)
    for bad in ["nan", "inf", "1e5", "1,5", "--1", ".5", "1." + "9" * 5 + "x", "9" * 400]:
        assert parse_one("OddHome", bad) == (None, True)


def test_parse_result() -> None:
    for good in ["H", "D", "A"]:
        assert parse_one("HTResult", good) == (good, False)
    assert parse_one("HTResult", " H ") == ("H", False)
    for bad in ["X", "h", "HD"]:
        assert parse_one("HTResult", bad) == (None, True)


def test_normalize_league() -> None:
    assert parse_one("Division", " e0 ") == ("E0", False)
    assert parse_one("Division", "SC0") == ("SC0", False)
    assert parse_one("Division", "E 0") == (None, True)
    assert parse_one("Division", "ABCDEFGHIJK") == (None, True)
    assert parse_one("Division", "") == (None, False)
