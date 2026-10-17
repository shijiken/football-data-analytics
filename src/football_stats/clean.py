"""Clean stage: strict parsers, team-name normalization and entity building (pure)."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

import polars as pl

from football_stats.source_contract import (
    COLUMN_SPECS,
    MATCH_STAGING_COLUMNS,
    ColumnSpec,
    Parser,
)

_MAX_SMALLINT = 32767


def _stripped(name: str) -> pl.Expr:
    """Column value stripped of surrounding whitespace; blank becomes null."""
    stripped = pl.col(name).str.strip_chars()
    return pl.when(stripped == "").then(None).otherwise(stripped)


def _plain(text: pl.Expr) -> tuple[pl.Expr, pl.Expr]:
    return text, pl.lit(False)


def _league(text: pl.Expr) -> tuple[pl.Expr, pl.Expr]:
    upper = text.str.to_uppercase()
    valid = upper.str.contains(r"^[A-Z0-9]{1,10}$").fill_null(False)
    return pl.when(valid).then(upper), text.is_not_null() & ~valid


def _date(text: pl.Expr) -> tuple[pl.Expr, pl.Expr]:
    shaped = text.str.contains(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$").fill_null(False)
    date = pl.when(shaped).then(text).str.strptime(pl.Date, "%Y-%m-%d", strict=False)
    valid = (date.is_not_null() & (date.dt.year() >= 1)).fill_null(False)
    return pl.when(valid).then(date), text.is_not_null() & ~valid


def _time(text: pl.Expr) -> tuple[pl.Expr, pl.Expr]:
    shaped = text.str.contains(r"^[0-9]{2}:[0-9]{2}:[0-9]{2}$").fill_null(False)
    time = pl.when(shaped).then(text).str.strptime(pl.Time, "%H:%M:%S", strict=False)
    valid = time.is_not_null().fill_null(False)
    return pl.when(valid).then(time), text.is_not_null() & ~valid


def _count(text: pl.Expr) -> tuple[pl.Expr, pl.Expr]:
    count = text.str.extract(r"^([0-9]+)(?:\.0+)?$", 1).cast(pl.Int64, strict=False)
    valid = (count.is_not_null() & (count <= _MAX_SMALLINT)).fill_null(False)
    return pl.when(valid).then(count).cast(pl.Int16), text.is_not_null() & ~valid


def _decimal(text: pl.Expr) -> tuple[pl.Expr, pl.Expr]:
    shaped = text.str.contains(r"^-?[0-9]+(\.[0-9]+)?$").fill_null(False)
    number = pl.when(shaped).then(text).cast(pl.Float64, strict=False)
    valid = number.is_finite().fill_null(False)
    return pl.when(valid).then(number), text.is_not_null() & ~valid


def _result(text: pl.Expr) -> tuple[pl.Expr, pl.Expr]:
    valid = text.is_in(["H", "D", "A"]).fill_null(False)
    return pl.when(valid).then(text), text.is_not_null() & ~valid


_PARSERS: dict[Parser, Callable[[pl.Expr], tuple[pl.Expr, pl.Expr]]] = {
    Parser.TEXT: _plain,
    Parser.TEAM: _plain,
    Parser.LEAGUE: _league,
    Parser.DATE: _date,
    Parser.TIME: _time,
    Parser.COUNT: _count,
    Parser.DECIMAL: _decimal,
    Parser.RESULT: _result,
}


def _parse_expressions(spec: ColumnSpec) -> tuple[pl.Expr, pl.Expr]:
    """Return ``(value, invalid)`` expressions for one column (data-model.md section 2.1)."""
    return _PARSERS[spec.parser](_stripped(spec.source_name))


def parse_frame(raw: pl.DataFrame) -> pl.DataFrame:
    """Add a typed ``<db_column>`` and a boolean ``<db_column>__invalid`` per column spec.

    Values are stripped first; blank means null and is not invalid. A present value that
    fails its strict parser becomes null with the invalid flag set. The original string
    columns are kept, plus a ``raw_record`` struct of every original column (untrimmed).

    Args:
        raw: Frame from :func:`football_stats.extract.read_source`.

    Returns:
        ``raw`` extended with the parsed columns and ``raw_record``.
    """
    original = [c for c in raw.columns if c != "source_row_number"]
    new_columns: list[pl.Expr] = [pl.struct(original).alias("raw_record")]
    for spec in COLUMN_SPECS:
        value, invalid = _parse_expressions(spec)
        new_columns.append(value.alias(spec.db_column))
        new_columns.append(invalid.alias(f"{spec.db_column}__invalid"))
    return raw.with_columns(new_columns)


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def team_key(name: str) -> str:
    """Normalized team identity: NFC, trimmed, whitespace collapsed, casefolded."""
    return _collapse_whitespace(unicodedata.normalize("NFC", name)).casefold()


def display_name(name: str) -> str:
    """Whitespace-normalized spelling of a team name (case preserved)."""
    return _collapse_whitespace(unicodedata.normalize("NFC", name))


def add_team_keys(frame: pl.DataFrame) -> pl.DataFrame:
    """Add ``home_team_key``, ``away_team_key`` and the matching ``*_display`` columns.

    The mapping is computed once per distinct raw name in Python and joined back; blank
    (null) names stay null.
    """
    names = (
        pl.concat([frame["home_team"], frame["away_team"]], how="vertical")
        .drop_nulls()
        .unique()
        .sort()
        .to_list()
    )
    if not names:
        return frame.with_columns(
            pl.lit(None, dtype=pl.String).alias(column)
            for column in (
                "home_team_key",
                "away_team_key",
                "home_team_display",
                "away_team_display",
            )
        )
    # Python treats e.g. U+001C..U+001F as whitespace while polars does not, so a name can be
    # non-blank for polars yet normalize to "". Such names are blank: null key, display, name.
    keys: list[str | None] = [team_key(n) or None for n in names]
    displays: list[str | None] = [display_name(n) or None for n in names]

    def mapped(column: str, values: list[str | None], alias: str) -> pl.Expr:
        return pl.col(column).replace_strict(names, values, default=None).alias(alias)

    mapped_frame = frame.with_columns(
        mapped("home_team", keys, "home_team_key"),
        mapped("away_team", keys, "away_team_key"),
        mapped("home_team", displays, "home_team_display"),
        mapped("away_team", displays, "away_team_display"),
    )
    return mapped_frame.with_columns(
        pl.when(pl.col("home_team_key").is_null())
        .then(None)
        .otherwise(pl.col("home_team"))
        .alias("home_team"),
        pl.when(pl.col("away_team_key").is_null())
        .then(None)
        .otherwise(pl.col("away_team"))
        .alias("away_team"),
    )


def canonical_team_names(accepted: pl.DataFrame) -> pl.DataFrame:
    """One row per team key with its canonical display name.

    The name is the most frequent display spelling among home and away occurrences; ties
    go to the smallest code-point string, so the result does not depend on row order.
    """
    occurrences = pl.concat(
        [
            accepted.select(
                pl.col("home_team_key").alias("team_key"),
                pl.col("home_team_display").alias("name"),
            ),
            accepted.select(
                pl.col("away_team_key").alias("team_key"),
                pl.col("away_team_display").alias("name"),
            ),
        ]
    )
    return (
        occurrences.group_by("team_key", "name")
        .agg(pl.len().alias("n"))
        .sort(["team_key", "n", "name"], descending=[False, True, False])
        .unique(subset="team_key", keep="first", maintain_order=True)
        .select("team_key", "name")
        .sort("team_key")
    )


@dataclass(frozen=True)
class CleanBatch:
    """Entities ready to load: leagues, teams and the match staging frame."""

    leagues: pl.DataFrame
    teams: pl.DataFrame
    matches: pl.DataFrame


def build_batch(accepted: pl.DataFrame, source_sha256: str) -> CleanBatch:
    """Build the league, team and match frames from accepted rows.

    Teams are sorted by key so identity values are deterministic on a fresh database;
    matches are sorted by ``source_row_number``.
    """
    matches = (
        accepted.with_columns(pl.lit(source_sha256).alias("source_sha256"))
        .select(MATCH_STAGING_COLUMNS)
        .sort("source_row_number")
    )
    return CleanBatch(
        leagues=accepted.select("league_code").unique().sort("league_code"),
        teams=canonical_team_names(accepted),
        matches=matches,
    )
