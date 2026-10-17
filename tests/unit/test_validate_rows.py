import json

import polars as pl
import pytest

from football_stats.validate import Partition
from tests.conftest import fixture_path
from tests.stages import partition_file, partition_rows

Outcome = str | dict[str, str]


def outcomes(part: Partition) -> dict[int, Outcome]:
    """Map source_row_number to ``"loaded"`` or ``{"reason", "detail"}``."""
    result: dict[int, Outcome] = {n: "loaded" for n in part.accepted["source_row_number"]}
    for row in part.excluded.iter_rows(named=True):
        result[row["source_row_number"]] = {"reason": row["reason"], "detail": row["detail"]}
    return result


def only_excluded(part: Partition) -> dict[str, str]:
    assert part.excluded.height == 1, part.excluded
    row = part.excluded.row(0, named=True)
    return {"reason": row["reason"], "detail": row["detail"]}


def test_edge_case_fixture_outcomes_match_expected() -> None:
    expected = json.loads(fixture_path("matches_edge_cases.expected.json").read_text("utf-8"))
    actual = outcomes(partition_file(fixture_path("matches_edge_cases.csv")))
    assert actual == {int(k): v for k, v in expected.items()}


@pytest.mark.parametrize(
    ("row", "detail"),
    [
        ({"Division": None}, "Division"),
        ({"MatchDate": None}, "MatchDate"),
        ({"HomeTeam": None}, "HomeTeam"),
        ({"AwayTeam": None}, "AwayTeam"),
        ({"HomeTeam": "   "}, "HomeTeam"),
        ({"HomeTeam": "\x1c"}, "HomeTeam"),
        ({"AwayTeam": " \x1f\x1e "}, "AwayTeam"),
        ({"Division": None, "AwayTeam": " "}, "Division, AwayTeam"),
        ({"__blank__": None}, "Division, MatchDate, HomeTeam, AwayTeam"),
    ],
)
def test_missing_required_field_excluded(row: dict[str, str | None], detail: str) -> None:
    assert only_excluded(partition_rows([row])) == {"reason": "missing_field", "detail": detail}


def test_invalid_date_excluded() -> None:
    part = partition_rows([{"MatchDate": "2021-02-30"}])
    assert only_excluded(part) == {"reason": "invalid_date", "detail": "2021-02-30"}


@pytest.mark.parametrize(
    ("row", "detail"),
    [
        ({"FTHome": None}, "FTHome"),
        ({"FTAway": " "}, "FTAway"),
        ({"FTResult": None}, "FTResult"),
        ({"FTHome": None, "FTAway": None, "FTResult": None}, "FTHome, FTAway, FTResult"),
    ],
)
def test_missing_result_excluded(row: dict[str, str | None], detail: str) -> None:
    assert only_excluded(partition_rows([row])) == {"reason": "missing_result", "detail": detail}


def test_optional_stats_missing_loaded_as_null() -> None:
    part = partition_rows([{}])
    assert part.excluded.height == 0
    row = part.accepted.row(0, named=True)
    for column in ("kickoff_time", "home_shots", "ht_result", "odd_home", "home_elo"):
        assert row[column] is None


@pytest.mark.parametrize(
    ("row", "detail"),
    [
        pytest.param({"Division": "E 0"}, "Division", id="division"),
        pytest.param({"FTHome": "2.5"}, "FTHome", id="fraction"),
        pytest.param({"HomeShots": "-1"}, "HomeShots", id="negative"),
        pytest.param({"HomeShots": "40000"}, "HomeShots", id="overflow"),
        pytest.param({"MatchTime": "25:00:00"}, "MatchTime", id="time"),
        pytest.param({"FTResult": "X"}, "FTResult", id="result"),
        pytest.param({"OddHome": "nan"}, "OddHome", id="nan_odd"),
        pytest.param({"OddHome": "nan", "FTHome": "2.5"}, "FTHome, OddHome", id="two_columns"),
    ],
)
def test_invalid_value_excluded_names_column(row: dict[str, str | None], detail: str) -> None:
    assert only_excluded(partition_rows([row])) == {"reason": "invalid_value", "detail": detail}


def test_same_team_after_normalization_excluded() -> None:
    part = partition_rows([{"HomeTeam": "Ajax", "AwayTeam": "ajax "}])
    assert only_excluded(part) == {"reason": "same_team", "detail": "ajax"}


def test_duplicate_keeps_first_excludes_rest() -> None:
    part = partition_rows([{"HomeTeam": "Beta"}, {}, {}, {}])
    assert part.accepted["source_row_number"].to_list() == [1, 2]
    assert outcomes(part)[3] == {"reason": "duplicate", "detail": "duplicate of source row 2"}
    assert outcomes(part)[4] == {"reason": "duplicate", "detail": "duplicate of source row 2"}


def test_duplicate_detected_after_normalization() -> None:
    part = partition_rows([{}, {"Division": " e0", "HomeTeam": "ARSENAL ", "AwayTeam": "chelsea"}])
    assert outcomes(part)[2] == {"reason": "duplicate", "detail": "duplicate of source row 1"}


def test_different_league_or_date_is_not_duplicate() -> None:
    part = partition_rows([{}, {"Division": "E1"}, {"MatchDate": "2020-09-13"}])
    assert part.excluded.height == 0


def test_reason_precedence() -> None:
    part = partition_rows(
        [
            {"HomeTeam": None, "MatchDate": "2021-02-30"},
            {"MatchDate": "2021-02-30", "FTHome": None},
            {"FTHome": None, "OddHome": "nan"},
            {"OddHome": "nan", "HomeTeam": "A", "AwayTeam": "a"},
            {"HomeTeam": "A", "AwayTeam": "a"},
        ]
    )
    reasons = {n: o["reason"] for n, o in outcomes(part).items() if not isinstance(o, str)}
    assert reasons == {
        1: "missing_field",
        2: "invalid_date",
        3: "missing_result",
        4: "invalid_value",
        5: "same_team",
    }


def test_invalid_row_never_wins_duplicate() -> None:
    part = partition_rows([{"HomeShots": "-1"}, {}, {}])
    result = outcomes(part)
    assert result[1] == {"reason": "invalid_value", "detail": "HomeShots"}
    assert result[2] == "loaded"
    assert result[3] == {"reason": "duplicate", "detail": "duplicate of source row 2"}


def test_excluded_carries_raw_record() -> None:
    part = partition_rows([{"HomeTeam": "  ", "OddHome": " 1.5 "}])
    record = part.excluded["raw_record"][0]
    assert isinstance(record, dict)
    assert record["HomeTeam"] == "  "
    assert record["OddHome"] == " 1.5 "
    assert record["MatchTime"] is None
    assert len(record) == 48
    assert part.excluded.schema["source_row_number"] == pl.UInt32
