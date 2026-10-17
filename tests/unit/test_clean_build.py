import polars as pl

from football_stats.clean import build_batch
from football_stats.source_contract import COLUMN_SPECS, Parser
from tests.conftest import fixture_path
from tests.stages import partition_file

SHA = "b" * 64
DTYPES = {
    Parser.LEAGUE: pl.String,
    Parser.DATE: pl.Date,
    Parser.TIME: pl.Time,
    Parser.COUNT: pl.Int16,
    Parser.DECIMAL: pl.Float64,
    Parser.RESULT: pl.String,
}


def test_build_entities() -> None:
    part = partition_file(fixture_path("matches_valid.csv"))
    batch = build_batch(part.accepted, SHA)

    assert batch.leagues["league_code"].to_list() == ["E0", "E1"]
    assert batch.teams.columns == ["team_key", "name"]
    assert dict(batch.teams.iter_rows()) == {
        "arsenal": "Arsenal",
        "chelsea": "Chelsea",
        "derby": "Derby",
        "everton": "Everton",
        "leeds": "Leeds",
        "liverpool": "Liverpool",
    }
    assert batch.teams["team_key"].to_list() == sorted(batch.teams["team_key"].to_list())

    matches = batch.matches
    assert matches.height == 12
    assert matches["source_row_number"].to_list() == list(range(1, 13))
    assert matches["source_sha256"].unique().to_list() == [SHA]
    for spec in COLUMN_SPECS:
        if spec.parser is Parser.TEAM:
            assert spec.db_column not in matches.columns
        else:
            assert matches.schema[spec.db_column] == DTYPES[spec.parser], spec.db_column
    assert matches["home_team_key"][0] == "arsenal"
    assert matches["away_team_key"][0] == "chelsea"
    assert matches["ft_home_goals"][0] == 2
    assert matches["kickoff_time"][2] is None


def test_team_in_multiple_leagues_single_team() -> None:
    part = partition_file(fixture_path("matches_valid.csv"))
    batch = build_batch(part.accepted, SHA)
    assert batch.teams["team_key"].to_list().count("everton") == 1
    leagues_of_everton = (
        batch.matches.filter(
            (pl.col("home_team_key") == "everton") | (pl.col("away_team_key") == "everton")
        )["league_code"]
        .unique()
        .sort()
        .to_list()
    )
    assert leagues_of_everton == ["E0", "E1"]
