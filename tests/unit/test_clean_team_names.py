import random

import polars as pl
import pytest

from football_stats.clean import canonical_team_names, display_name, team_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ajax ", "ajax"),
        (" ajax", "ajax"),
        ("AJAX", "ajax"),
        ("Man  City", "man city"),
        ("Man\tCity", "man city"),
        ("Man\u00a0City", "man city"),
        ("Man \u2003 City", "man city"),
        ("Mu\u0308nster", "m\u00fcnster"),
        ("M\u00fcnster", "m\u00fcnster"),
        ("Preu\u00dfen M\u00fcnster", "preussen m\u00fcnster"),
    ],
)
def test_team_key_normalizes_formatting(raw: str, expected: str) -> None:
    assert team_key(raw) == expected


@pytest.mark.parametrize("raw", ["Ajax ", "Man  City", "Preu\u00dfen", "Mu\u0308nster", "\u01c5 x"])
def test_team_key_is_idempotent(raw: str) -> None:
    assert team_key(team_key(raw)) == team_key(raw)


def test_display_name_keeps_case() -> None:
    assert display_name("  Man \t City ") == "Man City"
    assert display_name("Mu\u0308nster") == "M\u00fcnster"


def _accepted(homes: list[str], aways: list[str]) -> pl.DataFrame:
    frame = pl.DataFrame({"home_team": homes, "away_team": aways})
    return frame.with_columns(
        pl.col("home_team").map_elements(team_key, return_dtype=pl.String).alias("home_team_key"),
        pl.col("away_team").map_elements(team_key, return_dtype=pl.String).alias("away_team_key"),
        pl.col("home_team")
        .map_elements(display_name, return_dtype=pl.String)
        .alias("home_team_display"),
        pl.col("away_team")
        .map_elements(display_name, return_dtype=pl.String)
        .alias("away_team_display"),
    )


def test_canonical_name_most_frequent_then_lexicographic() -> None:
    accepted = _accepted(
        ["Colon Santa Fe", "Colon Santa FE", "X", "Y", "Beta", "beta"],
        ["Colon Santa Fe", "Z", "Colon Santa Fe", "W", "V", "U"],
    )
    names = dict(canonical_team_names(accepted).iter_rows())
    assert names["colon santa fe"] == "Colon Santa Fe"
    # 1-1 tie -> code-point smallest ("Beta" < "beta")
    assert names["beta"] == "Beta"


def test_canonical_name_independent_of_row_order() -> None:
    homes = ["Ajax", "AJAX", "ajax", "Ajax", "Feyenoord", "FEYENOORD", "feyenoord "]
    aways = ["Twente", "TWENTE", "Twente", "twente", "Ajax", "AJAX", "Utrecht"]
    expected = canonical_team_names(_accepted(homes, aways)).sort("team_key")
    rng = random.Random(0)
    for _ in range(5):
        order = list(range(len(homes)))
        rng.shuffle(order)
        shuffled = _accepted([homes[i] for i in order], [aways[i] for i in order])
        assert canonical_team_names(shuffled).sort("team_key").equals(expected)
