import re

from football_stats.source_contract import (
    COLUMN_SPECS,
    EXPECTED_COLUMNS,
    ColumnKind,
    ExclusionReason,
)

REAL_HEADER = (
    "Division,MatchDate,MatchTime,HomeTeam,AwayTeam,HomeElo,AwayElo,Form3Home,Form5Home,"
    "Form3Away,Form5Away,FTHome,FTAway,FTResult,HTHome,HTAway,HTResult,HomeShots,AwayShots,"
    "HomeTarget,AwayTarget,HomeFouls,AwayFouls,HomeCorners,AwayCorners,HomeYellow,AwayYellow,"
    "HomeRed,AwayRed,OddHome,OddDraw,OddAway,MaxHome,MaxDraw,MaxAway,Over25,Under25,MaxOver25,"
    "MaxUnder25,HandiSize,HandiHome,HandiAway,C_LTH,C_LTA,C_VHD,C_VAD,C_HTB,C_PHB"
)


def test_expected_columns_match_real_header() -> None:
    assert list(EXPECTED_COLUMNS) == REAL_HEADER.split(",")
    assert len(EXPECTED_COLUMNS) == 48


def test_every_expected_column_has_one_spec() -> None:
    assert [spec.source_name for spec in COLUMN_SPECS] == list(EXPECTED_COLUMNS)


def test_db_columns_unique_and_snake_case() -> None:
    db_columns = [spec.db_column for spec in COLUMN_SPECS]
    assert len(set(db_columns)) == len(db_columns)
    assert all(re.fullmatch(r"[a-z][a-z0-9]*(_[a-z0-9]+)*", c) for c in db_columns)


def test_kinds() -> None:
    by_kind: dict[ColumnKind, list[str]] = {kind: [] for kind in ColumnKind}
    for spec in COLUMN_SPECS:
        by_kind[spec.kind].append(spec.source_name)
    assert by_kind[ColumnKind.KEY] == ["Division", "MatchDate", "HomeTeam", "AwayTeam"]
    assert by_kind[ColumnKind.REQUIRED] == ["FTHome", "FTAway", "FTResult"]
    assert len(by_kind[ColumnKind.OPTIONAL]) == 41


def test_exclusion_reasons_in_precedence_order() -> None:
    assert [r.value for r in ExclusionReason] == [
        "missing_field",
        "invalid_date",
        "missing_result",
        "invalid_value",
        "same_team",
        "contract_violation",
        "duplicate",
    ]
