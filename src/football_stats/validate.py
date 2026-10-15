"""Validate stage: header check and row partitioning with exclusion reasons (pure)."""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

import polars as pl

from football_stats.data_contract import DataContract, find_violations
from football_stats.errors import AccountingError, MalformedSourceError, MissingColumnsError
from football_stats.source_contract import (
    COLUMN_SPECS,
    EXPECTED_COLUMNS,
    ColumnKind,
    ColumnSpec,
    ExclusionReason,
)

_DUPLICATE_KEY = ["league_code", "match_date", "home_team_key", "away_team_key"]
_EXCLUDED_COLUMNS = ["source_row_number", "reason", "detail", "raw_record"]


def check_header(header: Sequence[str]) -> list[str]:
    """Check the raw header against the source contract (FR-002).

    Returns:
        Extra column names (not in the contract), in file order, for a warning.

    Raises:
        MalformedSourceError: If a header name occurs more than once.
        MissingColumnsError: If expected columns are missing (all are named).
    """
    duplicates = sorted(name for name, n in Counter(header).items() if n > 1)
    if duplicates:
        raise MalformedSourceError(f"Source header has duplicate columns: {', '.join(duplicates)}")
    present = set(header)
    missing = tuple(name for name in EXPECTED_COLUMNS if name not in present)
    if missing:
        raise MissingColumnsError(missing)
    expected = set(EXPECTED_COLUMNS)
    return [name for name in header if name not in expected]


@dataclass(frozen=True)
class Partition:
    """Accepted and excluded rows of one source file.

    ``excluded`` has ``source_row_number``, ``reason``, ``detail`` and ``raw_record``.
    ``violations`` is the long ``(source_row_number, rule_id)`` frame of the data contract and
    ``rule_ids`` the contract's rule ids in contract order.
    """

    accepted: pl.DataFrame
    excluded: pl.DataFrame
    rows_read: int
    violations: pl.DataFrame = field(
        default_factory=lambda: pl.DataFrame(
            schema={"source_row_number": pl.UInt32, "rule_id": pl.String}
        )
    )
    rule_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Fail loudly if any row is unaccounted for (never ``assert``: ``-O`` strips it)."""
        total = self.accepted.height + self.excluded.height
        if total != self.rows_read:
            raise AccountingError(
                f"rows_read={self.rows_read} but accepted+excluded={total}; "
                "refusing to load with unaccounted rows"
            )

    def counts_by_reason(self) -> dict[str, int]:
        """Return the exclusion count for every reason (zeros included)."""
        counts = dict.fromkeys((reason.value for reason in ExclusionReason), 0)
        for reason, n in self.excluded.group_by("reason").len().iter_rows():
            counts[reason] = n
        return counts

    def violations_by_rule(self) -> dict[str, int]:
        """Return the number of rows violating each rule (zeros included, contract order)."""
        counts = dict.fromkeys(self.rule_ids, 0)
        for rule_id, n in self.violations.group_by("rule_id").len().iter_rows():
            counts[rule_id] = n
        return counts


def _names_where(specs: list[ColumnSpec], condition: str) -> pl.Expr:
    """Comma-joined source names (contract order) of the specs matching ``condition``."""
    parts: list[pl.Expr] = []
    for spec in specs:
        value_missing = pl.col(spec.db_column).is_null()
        invalid = pl.col(f"{spec.db_column}__invalid")
        hit = (value_missing & ~invalid) if condition == "missing" else invalid
        parts.append(pl.when(hit).then(pl.lit(spec.source_name)))
    return pl.concat_list(parts).list.drop_nulls().list.join(", ")


def partition(parsed: pl.DataFrame, *, contract: DataContract, run_date: dt.date) -> Partition:
    """Split parsed rows into accepted and excluded (one reason per excluded row).

    Rules run in a fixed order (001 research.md R6, 002 research.md R4): ``missing_field``,
    ``invalid_date``, ``missing_result``, ``invalid_value``, ``same_team``, then
    ``contract_violation`` among the rows that passed all earlier rules (``detail`` lists every
    violated rule id in contract order), then ``duplicate`` among the rows that passed all
    earlier rules, keeping the lowest ``source_row_number``.

    Args:
        parsed: Output of the parse and team-key stages.
        contract: The data contract to enforce (``PRE_CONTRACT`` enforces nothing).
        run_date: UTC date the run started (upper bound of ``date_in_range``).

    Raises:
        ContractSchemaError: If the contract cannot be evaluated on ``parsed``.
    """
    key_specs = [s for s in COLUMN_SPECS if s.kind is ColumnKind.KEY]
    result_specs = [s for s in COLUMN_SPECS if s.kind is ColumnKind.REQUIRED]
    checked = parsed.with_columns(
        _names_where(key_specs, "missing").alias("_missing_key"),
        _names_where(result_specs, "missing").alias("_missing_result"),
        _names_where(list(COLUMN_SPECS), "invalid").alias("_invalid_value"),
    )
    reason = (
        pl.when(pl.col("_missing_key") != "")
        .then(pl.lit(ExclusionReason.MISSING_FIELD.value))
        .when(pl.col("match_date__invalid"))
        .then(pl.lit(ExclusionReason.INVALID_DATE.value))
        .when(pl.col("_missing_result") != "")
        .then(pl.lit(ExclusionReason.MISSING_RESULT.value))
        .when(pl.col("_invalid_value") != "")
        .then(pl.lit(ExclusionReason.INVALID_VALUE.value))
        .when(pl.col("home_team_key") == pl.col("away_team_key"))
        .then(pl.lit(ExclusionReason.SAME_TEAM.value))
    )
    detail = (
        pl.when(pl.col("_missing_key") != "")
        .then(pl.col("_missing_key"))
        .when(pl.col("match_date__invalid"))
        .then(pl.col("MatchDate"))
        .when(pl.col("_missing_result") != "")
        .then(pl.col("_missing_result"))
        .when(pl.col("_invalid_value") != "")
        .then(pl.col("_invalid_value"))
        .when(pl.col("home_team_key") == pl.col("away_team_key"))
        .then(pl.col("home_team_key"))
    )
    checked = checked.with_columns(reason.alias("reason"), detail.alias("detail"))

    # Contract rules see only rows that passed reasons 1-5, and run before duplicate detection:
    # a broken first copy must not hide a valid later copy of the same match.
    passed_column_checks = checked.filter(pl.col("reason").is_null())
    violations = find_violations(passed_column_checks, contract, run_date)
    violated = (
        violations.group_by("source_row_number", maintain_order=True)
        .agg(pl.col("rule_id").str.join(", ").alias("_violated"))
        .lazy()
    )
    checked = (
        checked.lazy()
        .join(violated, on="source_row_number", how="left")
        .with_columns(
            pl.when(pl.col("_violated").is_not_null())
            .then(pl.lit(ExclusionReason.CONTRACT_VIOLATION.value))
            .otherwise(pl.col("reason"))
            .alias("reason"),
            pl.coalesce(pl.col("_violated"), pl.col("detail")).alias("detail"),
        )
        .drop("_violated")
        .collect()
    )

    candidates = checked.filter(pl.col("reason").is_null()).with_columns(
        pl.col("source_row_number").min().over(_DUPLICATE_KEY).alias("_first_row")
    )
    duplicates = candidates.filter(
        pl.col("source_row_number") != pl.col("_first_row")
    ).with_columns(
        pl.lit(ExclusionReason.DUPLICATE.value).alias("reason"),
        (pl.lit("duplicate of source row ") + pl.col("_first_row").cast(pl.String)).alias("detail"),
    )
    accepted = candidates.filter(pl.col("source_row_number") == pl.col("_first_row"))
    excluded = pl.concat(
        [
            checked.filter(pl.col("reason").is_not_null()).select(_EXCLUDED_COLUMNS),
            duplicates.select(_EXCLUDED_COLUMNS),
        ]
    ).sort("source_row_number")
    helper = ["_missing_key", "_missing_result", "_invalid_value", "reason", "detail", "_first_row"]
    return Partition(
        accepted=accepted.drop(helper),
        excluded=excluded,
        rows_read=parsed.height,
        violations=violations,
        rule_ids=contract.rule_ids,
    )
