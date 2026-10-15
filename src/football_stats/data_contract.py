"""The versioned match data contract: cross-field rules evaluated with pandera (pure).

The contract is one declarative object (:data:`CURRENT_CONTRACT`). Each rule is a conjunction of
*terms*; a term is skipped when any of its inputs is missing, so a rule is violated only when a
term with all inputs present is false (research.md R3). The same semantics are enforced by the
``matches_contract_<rule_id>`` CHECK constraints (migration ``0003``).

This module reads no clock, database, network or file: the run date is always an argument.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

import pandera.errors
import pandera.polars as pa
import polars as pl
from pandera.config import ValidationDepth, config_context
from pandera.polars import PolarsData

from football_stats.errors import ContractSchemaError
from football_stats.source_contract import COLUMN_SPECS, ColumnKind, ColumnSpec, Parser

CONTRACT_VERSION = 1

_MIN_DATE = dt.date(2000, 1, 1)
_DTYPES: dict[Parser, type[pl.DataType]] = {
    Parser.COUNT: pl.Int16,
    Parser.DECIMAL: pl.Float64,
    Parser.RESULT: pl.String,
    Parser.DATE: pl.Date,
}
_SPECS: dict[str, ColumnSpec] = {spec.db_column: spec for spec in COLUMN_SPECS}
_VIOLATION_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "source_row_number": pl.UInt32,
    "rule_id": pl.String,
}


@dataclass(frozen=True)
class Term:
    """One comparison of a rule.

    Attributes:
        inputs: Database column names the condition reads; the term is skipped if any is null.
        condition: Builds the polars expression that is true when the term holds.
    """

    inputs: tuple[str, ...]
    condition: Callable[[dt.date], pl.Expr]


@dataclass(frozen=True)
class ContractRule:
    """A named rule: a conjunction of terms with a stable id and a description."""

    rule_id: str
    description: str
    terms: tuple[Term, ...]

    @property
    def inputs(self) -> tuple[str, ...]:
        """Union of the term inputs, in first-seen order."""
        return tuple(dict.fromkeys(column for term in self.terms for column in term.inputs))

    def passes(self, run_date: dt.date) -> pl.Expr:
        """Return a boolean expression that is true when the rule holds (never null)."""
        return pl.all_horizontal(
            [
                pl.any_horizontal([pl.col(c).is_null() for c in term.inputs])
                | term.condition(run_date)
                for term in self.terms
            ]
        )


@dataclass(frozen=True)
class DataContract:
    """A versioned, ordered set of rules."""

    version: int
    rules: tuple[ContractRule, ...]

    @property
    def rule_ids(self) -> tuple[str, ...]:
        """Rule ids in contract order."""
        return tuple(rule.rule_id for rule in self.rules)


def _le(smaller: str, larger: str) -> Term:
    return Term((smaller, larger), lambda _d: pl.col(smaller) <= pl.col(larger))


def _result_term(home: str, away: str, result: str) -> Term:
    def condition(_run_date: dt.date) -> pl.Expr:
        h, a = pl.col(home), pl.col(away)
        expected = (
            pl.when(h > a).then(pl.lit("H")).when(h == a).then(pl.lit("D")).otherwise(pl.lit("A"))
        )
        return expected == pl.col(result)

    return Term((home, away, result), condition)


def _odd_at_least_one(column: str) -> Term:
    return Term((column,), lambda _d: pl.col(column) >= 1.0)


def _max_ge_avg(avg: str, maximum: str) -> Term:
    return Term((avg, maximum), lambda _d: pl.col(maximum) >= pl.col(avg))


def _date_in_range(run_date: dt.date) -> pl.Expr:
    return (pl.col("match_date") >= _MIN_DATE) & (pl.col("match_date") <= pl.lit(run_date))


_ODDS = ("odd_home", "odd_draw", "odd_away", "max_odd_home", "max_odd_draw", "max_odd_away")

RULES: tuple[ContractRule, ...] = (
    ContractRule(
        "ht_le_ft_home",
        "Half-time home goals do not exceed full-time home goals",
        (_le("ht_home_goals", "ft_home_goals"),),
    ),
    ContractRule(
        "ht_le_ft_away",
        "Half-time away goals do not exceed full-time away goals",
        (_le("ht_away_goals", "ft_away_goals"),),
    ),
    ContractRule(
        "ft_result_consistent",
        "Full-time result matches the full-time score",
        (_result_term("ft_home_goals", "ft_away_goals", "ft_result"),),
    ),
    ContractRule(
        "ht_result_consistent",
        "Half-time result matches the half-time score",
        (_result_term("ht_home_goals", "ht_away_goals", "ht_result"),),
    ),
    ContractRule(
        "sot_le_shots_home",
        "Home shots on target do not exceed home shots",
        (_le("home_shots_on_target", "home_shots"),),
    ),
    ContractRule(
        "sot_le_shots_away",
        "Away shots on target do not exceed away shots",
        (_le("away_shots_on_target", "away_shots"),),
    ),
    ContractRule(
        "odds_min",
        "Every pre-match decimal odd is at least 1.0",
        tuple(_odd_at_least_one(column) for column in _ODDS),
    ),
    ContractRule(
        "max_odds_ge_avg",
        "Each maximum odd is at least the corresponding average odd",
        (
            _max_ge_avg("odd_home", "max_odd_home"),
            _max_ge_avg("odd_draw", "max_odd_draw"),
            _max_ge_avg("odd_away", "max_odd_away"),
        ),
    ),
    ContractRule(
        "date_in_range",
        "Match date is between 2000-01-01 and the run date, inclusive",
        (Term(("match_date",), _date_in_range),),
    ),
)

CURRENT_CONTRACT = DataContract(CONTRACT_VERSION, RULES)

#: Version 0: feature 001's per-column checks only (no cross-field rules).
PRE_CONTRACT = DataContract(0, ())


def _rule_check(rule: ContractRule, run_date: dt.date) -> pa.Check:
    def check(data: PolarsData) -> pl.LazyFrame:
        return data.lazyframe.select(rule.passes(run_date).alias("check_output"))

    # No ``error=``: with it the failure case reports the message instead of the check name.
    return pa.Check(check, name=rule.rule_id)


def build_schema(contract: DataContract, run_date: dt.date) -> pa.DataFrameSchema:
    """Compile ``contract`` to a pandera schema (declared inputs, dtypes, one check per rule)."""
    inputs = dict.fromkeys(column for rule in contract.rules for column in rule.inputs)
    columns = {
        name: pa.Column(
            _DTYPES[_SPECS[name].parser],
            nullable=_SPECS[name].kind is ColumnKind.OPTIONAL,
        )
        for name in inputs
    }
    return pa.DataFrameSchema(
        columns,
        checks=[_rule_check(rule, run_date) for rule in contract.rules],
        strict=False,
        coerce=False,
    )


def _empty_violations() -> pl.DataFrame:
    return pl.DataFrame(schema=_VIOLATION_SCHEMA)


def find_violations(frame: pl.DataFrame, contract: DataContract, run_date: dt.date) -> pl.DataFrame:
    """Return one row per (source row, violated rule), sorted by row then contract order.

    Args:
        frame: Parsed rows with ``source_row_number`` and the contract's input columns.
        contract: The contract to evaluate.
        run_date: Upper bound of ``date_in_range`` (the UTC date the run started).

    Returns:
        A frame with ``source_row_number`` (UInt32) and ``rule_id`` (String).

    Raises:
        ContractSchemaError: If the frame does not match the contract's declared columns or
            dtypes, or a rule failed to evaluate (a programming error, never bad data).
    """
    if not contract.rules or frame.height == 0:
        return _empty_violations()
    schema = build_schema(contract, run_date)
    inputs = ["source_row_number", *schema.columns]
    subset = frame.select(inputs)
    rule_position = {rule_id: i for i, rule_id in enumerate(contract.rule_ids)}
    # Forced context: PANDERA_VALIDATION_ENABLED=False must not silently disable the contract.
    with config_context(validation_enabled=True, validation_depth=ValidationDepth.SCHEMA_AND_DATA):
        try:
            schema.validate(subset, lazy=True)
        except pandera.errors.SchemaErrors as exc:
            cases = exc.failure_cases
            if not isinstance(cases, pl.DataFrame):
                raise ContractSchemaError(
                    f"unexpected pandera failure-case type {type(cases).__name__}"
                ) from exc
            return _violations_from(cases, subset, rule_position)
    return _empty_violations()


def _violations_from(
    cases: pl.DataFrame, subset: pl.DataFrame, rule_position: dict[str, int]
) -> pl.DataFrame:
    """Map pandera failure cases to (row number, rule id); raise on any non-rule failure."""
    for case in cases.iter_rows(named=True):
        if (
            case["schema_context"] != "DataFrameSchema"
            or case["check"] not in rule_position
            or case["index"] is None
        ):
            raise ContractSchemaError(
                "contract could not be evaluated on the parsed frame: "
                f"context={case['schema_context']!r} column={case['column']!r} "
                f"check={case['check']!r} detail={case['failure_case']!r}"
            )
    positions = pl.DataFrame(
        {
            "index": pl.Series(range(subset.height), dtype=cases.schema["index"]),
            "source_row_number": subset["source_row_number"],
        }
    )
    order = pl.DataFrame(
        {"check": list(rule_position), "_pos": list(rule_position.values())},
        schema={"check": pl.String, "_pos": pl.Int64},
    )
    return (
        cases.select("index", "check")
        .join(positions, on="index", how="left")
        .join(order, on="check", how="left")
        .sort("source_row_number", "_pos")
        .select(
            pl.col("source_row_number").cast(pl.UInt32),
            pl.col("check").alias("rule_id"),
        )
    )
