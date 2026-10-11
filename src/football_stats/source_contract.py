"""The source column contract: expected header, per-column kind/parser, exclusion reasons."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, StrEnum

PINNED_SOURCE_URL = (
    "https://raw.githubusercontent.com/xgabora/Club-Football-Match-Data/"
    "25882a58a736daf7ece3781940eac17ae1117a66/data/Matches.csv"
)


class ColumnKind(Enum):
    """How a missing value in a column is treated."""

    KEY = "key"
    REQUIRED = "required"
    OPTIONAL = "optional"


class Parser(Enum):
    """Strict parser applied to the column's text (data-model.md section 2.1)."""

    TEXT = "text"
    LEAGUE = "league"
    TEAM = "team"
    DATE = "date"
    TIME = "time"
    COUNT = "count"
    DECIMAL = "decimal"
    RESULT = "result"


class ExclusionReason(StrEnum):
    """Why a row was excluded, in rule-precedence order (research.md R6)."""

    MISSING_FIELD = "missing_field"
    INVALID_DATE = "invalid_date"
    MISSING_RESULT = "missing_result"
    INVALID_VALUE = "invalid_value"
    SAME_TEAM = "same_team"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class ColumnSpec:
    """Contract for one source column."""

    source_name: str
    db_column: str
    kind: ColumnKind
    parser: Parser


_K, _R, _O = ColumnKind.KEY, ColumnKind.REQUIRED, ColumnKind.OPTIONAL
_C, _D = Parser.COUNT, Parser.DECIMAL

COLUMN_SPECS: tuple[ColumnSpec, ...] = (
    ColumnSpec("Division", "league_code", _K, Parser.LEAGUE),
    ColumnSpec("MatchDate", "match_date", _K, Parser.DATE),
    ColumnSpec("MatchTime", "kickoff_time", _O, Parser.TIME),
    ColumnSpec("HomeTeam", "home_team", _K, Parser.TEAM),
    ColumnSpec("AwayTeam", "away_team", _K, Parser.TEAM),
    ColumnSpec("HomeElo", "home_elo", _O, _D),
    ColumnSpec("AwayElo", "away_elo", _O, _D),
    ColumnSpec("Form3Home", "home_form3", _O, _C),
    ColumnSpec("Form5Home", "home_form5", _O, _C),
    ColumnSpec("Form3Away", "away_form3", _O, _C),
    ColumnSpec("Form5Away", "away_form5", _O, _C),
    ColumnSpec("FTHome", "ft_home_goals", _R, _C),
    ColumnSpec("FTAway", "ft_away_goals", _R, _C),
    ColumnSpec("FTResult", "ft_result", _R, Parser.RESULT),
    ColumnSpec("HTHome", "ht_home_goals", _O, _C),
    ColumnSpec("HTAway", "ht_away_goals", _O, _C),
    ColumnSpec("HTResult", "ht_result", _O, Parser.RESULT),
    ColumnSpec("HomeShots", "home_shots", _O, _C),
    ColumnSpec("AwayShots", "away_shots", _O, _C),
    ColumnSpec("HomeTarget", "home_shots_on_target", _O, _C),
    ColumnSpec("AwayTarget", "away_shots_on_target", _O, _C),
    ColumnSpec("HomeFouls", "home_fouls", _O, _C),
    ColumnSpec("AwayFouls", "away_fouls", _O, _C),
    ColumnSpec("HomeCorners", "home_corners", _O, _C),
    ColumnSpec("AwayCorners", "away_corners", _O, _C),
    ColumnSpec("HomeYellow", "home_yellow_cards", _O, _C),
    ColumnSpec("AwayYellow", "away_yellow_cards", _O, _C),
    ColumnSpec("HomeRed", "home_red_cards", _O, _C),
    ColumnSpec("AwayRed", "away_red_cards", _O, _C),
    ColumnSpec("OddHome", "odd_home", _O, _D),
    ColumnSpec("OddDraw", "odd_draw", _O, _D),
    ColumnSpec("OddAway", "odd_away", _O, _D),
    ColumnSpec("MaxHome", "max_odd_home", _O, _D),
    ColumnSpec("MaxDraw", "max_odd_draw", _O, _D),
    ColumnSpec("MaxAway", "max_odd_away", _O, _D),
    ColumnSpec("Over25", "odd_over25", _O, _D),
    ColumnSpec("Under25", "odd_under25", _O, _D),
    ColumnSpec("MaxOver25", "max_odd_over25", _O, _D),
    ColumnSpec("MaxUnder25", "max_odd_under25", _O, _D),
    ColumnSpec("HandiSize", "handicap_size", _O, _D),
    ColumnSpec("HandiHome", "handicap_odd_home", _O, _D),
    ColumnSpec("HandiAway", "handicap_odd_away", _O, _D),
    ColumnSpec("C_LTH", "cluster_lth", _O, _D),
    ColumnSpec("C_LTA", "cluster_lta", _O, _D),
    ColumnSpec("C_VHD", "cluster_vhd", _O, _D),
    ColumnSpec("C_VAD", "cluster_vad", _O, _D),
    ColumnSpec("C_HTB", "cluster_htb", _O, _D),
    ColumnSpec("C_PHB", "cluster_phb", _O, _D),
)

EXPECTED_COLUMNS: tuple[str, ...] = tuple(spec.source_name for spec in COLUMN_SPECS)

_KEY_DB_COLUMNS = ("league_code", "match_date", "kickoff_time")

#: Columns of the ``matches`` staging frame/table, in order: the natural-key parts, the two
#: team keys (resolved to ids in SQL), every other source value, then provenance.
MATCH_STAGING_COLUMNS: tuple[str, ...] = (
    *_KEY_DB_COLUMNS,
    "home_team_key",
    "away_team_key",
    *(
        spec.db_column
        for spec in COLUMN_SPECS
        if spec.parser is not Parser.TEAM and spec.db_column not in _KEY_DB_COLUMNS
    ),
    "source_sha256",
    "source_row_number",
)

_SQL_TYPES = {
    Parser.TEXT: "text",
    Parser.LEAGUE: "text",
    Parser.DATE: "date",
    Parser.TIME: "time",
    Parser.COUNT: "smallint",
    Parser.DECIMAL: "double precision",
    Parser.RESULT: "char(1)",
}

#: PostgreSQL type of each staging column.
MATCH_STAGING_SQL_TYPES: dict[str, str] = {
    "home_team_key": "text",
    "away_team_key": "text",
    "source_sha256": "char(64)",
    "source_row_number": "integer",
    **{s.db_column: _SQL_TYPES[s.parser] for s in COLUMN_SPECS if s.parser is not Parser.TEAM},
}

#: Columns that identify a match (the upsert conflict target, resolved to ids in SQL).
MATCH_KEY_COLUMNS: tuple[str, ...] = ("league_code", "match_date", "home_team_key", "away_team_key")
