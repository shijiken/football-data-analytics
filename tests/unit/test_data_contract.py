"""Unit tests for the data contract: rule table, pandera schema and violation mapping."""

import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

import pandera.polars as pa
import polars as pl
import pytest
from football_stats.data_contract import (
    CONTRACT_VERSION,
    CURRENT_CONTRACT,
    PRE_CONTRACT,
    ContractRule,
    DataContract,
    Term,
    build_schema,
    find_violations,
)
from pandera.config import config_context
from pandera.engines import polars_engine

from football_stats import clean
from football_stats.errors import ContractSchemaError
from football_stats.source_contract import COLUMN_SPECS, EXPECTED_COLUMNS, ColumnKind, Parser
from tests.conftest import FIXTURES_DIR
from tests.contract_cases import CASE_IDS, CONTRACT_CASES, ContractCase, materialize
from tests.stages import CONTRACT_STARTED_AT, RUN_DATE, partition_rows

REPO_ROOT = Path(__file__).resolve().parents[2]
CASES = {case.case_id: case for case in CONTRACT_CASES}
VIOLATION_SCHEMA = {"source_row_number": pl.UInt32, "rule_id": pl.String}


def parse_rows(rows: list[dict[str, str | None]], numbers: list[int] | None = None) -> pl.DataFrame:
    """Parse source-string rows into the real parsed frame (the input of ``find_violations``)."""
    raw = pl.DataFrame(rows, schema=dict.fromkeys(EXPECTED_COLUMNS, pl.String)).with_columns(
        pl.Series("source_row_number", numbers or range(1, len(rows) + 1), dtype=pl.UInt32)
    )
    return clean.add_team_keys(clean.parse_frame(raw))


def _row(case_id: str) -> dict[str, str | None]:
    return materialize(CASES[case_id], RUN_DATE)


# --- run-date guard -------------------------------------------------------------------------

_DATE_AT_START = re.compile(rb"^[^,\r\n]*,(\d{4}-\d{2}-\d{2})")


def test_run_date_after_all_fixture_dates() -> None:
    """The default test run date is on/after every fixture match date (except 2999 rows).

    Fixture files are scanned as raw bytes with a regex: some are intentionally invalid UTF-8
    or ragged, so the pipeline's strict CSV reader cannot be used here.
    """
    synthetic_future = dt.date(2999, 1, 1)
    latest = dt.date.min
    for path in sorted(FIXTURES_DIR.glob("*.csv")):
        for line in path.read_bytes().splitlines():
            found = _DATE_AT_START.match(line)
            if found is None:
                continue
            try:
                match_date = dt.date.fromisoformat(found.group(1).decode("ascii"))
            except ValueError:
                continue
            if match_date != synthetic_future:
                latest = max(latest, match_date)
    assert latest != dt.date.min
    assert latest <= RUN_DATE
    assert latest <= CONTRACT_STARTED_AT.date()


# --- contract definition --------------------------------------------------------------------


def test_contract_rule_ids_stable() -> None:
    assert CONTRACT_VERSION == 1 == CURRENT_CONTRACT.version
    assert CURRENT_CONTRACT.rule_ids == (
        "ht_le_ft_home",
        "ht_le_ft_away",
        "ft_result_consistent",
        "ht_result_consistent",
        "sot_le_shots_home",
        "sot_le_shots_away",
        "odds_min",
        "max_odds_ge_avg",
        "date_in_range",
    )
    db_columns = {spec.db_column for spec in COLUMN_SPECS}
    for rule in CURRENT_CONTRACT.rules:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", rule.rule_id)
        assert len(rule.rule_id) <= 46
        assert rule.description
        assert set(rule.inputs) <= db_columns
    terms = {rule.rule_id: len(rule.terms) for rule in CURRENT_CONTRACT.rules}
    assert terms["odds_min"] == 6
    assert terms["max_odds_ge_avg"] == 3
    assert all(
        n == 1 for rule_id, n in terms.items() if rule_id not in {"odds_min", "max_odds_ge_avg"}
    )


def test_schema_declares_rule_inputs_with_parser_dtypes() -> None:
    schema = build_schema(CURRENT_CONTRACT, RUN_DATE)
    inputs = {column for rule in CURRENT_CONTRACT.rules for column in rule.inputs}
    assert set(schema.columns) == inputs
    expected_dtype = {
        Parser.COUNT: pl.Int16,
        Parser.DECIMAL: pl.Float64,
        Parser.RESULT: pl.String,
        Parser.DATE: pl.Date,
    }
    specs = {spec.db_column: spec for spec in COLUMN_SPECS}
    for name, column in schema.columns.items():
        spec = specs[name]
        assert column.dtype == polars_engine.Engine.dtype(expected_dtype[spec.parser]), name
        assert column.nullable is (spec.kind is ColumnKind.OPTIONAL), name
    assert {"ft_home_goals", "ft_away_goals", "ft_result", "match_date"}.isdisjoint(
        name for name, column in schema.columns.items() if column.nullable
    )
    assert [check.name for check in schema.checks] == list(CURRENT_CONTRACT.rule_ids)
    assert isinstance(schema, pa.DataFrameSchema)


def test_pre_contract_is_version_zero_without_rules() -> None:
    assert PRE_CONTRACT.version == 0
    assert PRE_CONTRACT.rules == ()
    assert PRE_CONTRACT.rule_ids == ()


@pytest.mark.parametrize("case", CONTRACT_CASES, ids=CASE_IDS)
def test_contract_case_outcome(case: ContractCase) -> None:
    part = partition_rows([materialize(case, RUN_DATE)])
    if not case.violated:
        assert part.accepted.height == 1
        assert part.excluded.height == 0
        return
    assert part.accepted.height == 0
    assert part.excluded.height == 1
    row = part.excluded.row(0, named=True)
    assert row["reason"] == "contract_violation"
    assert row["detail"] == ", ".join(case.violated)


def test_date_in_range_uses_injected_run_date() -> None:
    row: dict[str, str | None] = {**_row("valid_full"), "MatchDate": "2026-10-15"}
    accepted = partition_rows([row], run_date=dt.date(2026, 10, 15))
    assert accepted.accepted.height == 1
    rejected = partition_rows([row], run_date=dt.date(2026, 10, 14))
    assert rejected.excluded["reason"].to_list() == ["contract_violation"]
    assert rejected.excluded["detail"].to_list() == ["date_in_range"]


# --- find_violations --------------------------------------------------------------------------


def test_find_violations_sorted_long_format() -> None:
    frame = parse_rows(
        [_row("multi_rule"), _row("valid_full"), _row("max_below_avg")], numbers=[3, 2, 1]
    )
    found = find_violations(frame, CURRENT_CONTRACT, RUN_DATE)
    assert found.schema == VIOLATION_SCHEMA
    assert found.rows() == [
        (1, "max_odds_ge_avg"),
        (3, "ht_le_ft_home"),
        (3, "ft_result_consistent"),
    ]


def test_find_violations_empty_contract_or_frame() -> None:
    frame = parse_rows([_row("multi_rule")])
    no_rules = find_violations(frame, PRE_CONTRACT, RUN_DATE)
    assert no_rules.schema == VIOLATION_SCHEMA
    assert no_rules.height == 0
    no_rows = find_violations(frame.clear(), CURRENT_CONTRACT, RUN_DATE)
    assert no_rows.schema == VIOLATION_SCHEMA
    assert no_rows.height == 0


def test_find_violations_ignores_disabled_pandera_config() -> None:
    frame = parse_rows([_row("ft_result_wrong")])
    with config_context(validation_enabled=False):
        found = find_violations(frame, CURRENT_CONTRACT, RUN_DATE)
    assert found["rule_id"].to_list() == ["ft_result_consistent"]


def test_find_violations_wrong_dtype_raises_contract_schema_error() -> None:
    frame = parse_rows([_row("valid_full")]).with_columns(pl.col("ft_home_goals").cast(pl.Int64))
    with pytest.raises(ContractSchemaError):
        find_violations(frame, CURRENT_CONTRACT, RUN_DATE)


def test_find_violations_raising_rule_raises_contract_schema_error() -> None:
    broken = DataContract(
        98,
        (
            ContractRule(
                "broken",
                "test only: reads a column outside its declared inputs",
                (Term(("ft_home_goals",), lambda _d: pl.col("not_an_input") > 0),),
            ),
        ),
    )
    with pytest.raises(ContractSchemaError):
        find_violations(parse_rows([_row("valid_full")]), broken, RUN_DATE)


def test_find_violations_is_complete_for_many_failures() -> None:
    n = 50_000
    one = parse_rows([_row("ft_result_wrong")])
    frame = pl.concat([one] * n).with_columns(
        pl.int_range(1, n + 1, dtype=pl.UInt32).alias("source_row_number")
    )
    found = find_violations(frame, CURRENT_CONTRACT, RUN_DATE)
    assert found.height == n
    assert found["source_row_number"].to_list() == list(range(1, n + 1))
    assert set(found["rule_id"]) == {"ft_result_consistent"}


_ENV_SCRIPT = """
import datetime as dt
import polars as pl
from football_stats import clean
from football_stats.data_contract import CURRENT_CONTRACT, find_violations
from football_stats.source_contract import EXPECTED_COLUMNS
from tests.contract_cases import CONTRACT_CASES, materialize

run_date = dt.date(2023, 6, 30)
case = next(c for c in CONTRACT_CASES if c.case_id == "ft_result_wrong")
raw = pl.DataFrame([materialize(case, run_date)], schema=dict.fromkeys(EXPECTED_COLUMNS, pl.String))
raw = raw.with_columns(pl.int_range(1, 2, dtype=pl.UInt32).alias("source_row_number"))
frame = clean.add_team_keys(clean.parse_frame(raw))
print(",".join(find_violations(frame, CURRENT_CONTRACT, run_date)["rule_id"].to_list()))
"""


def test_find_violations_ignores_pandera_env_disable() -> None:
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": f"{REPO_ROOT / 'src'}:{REPO_ROOT}",
        "PANDERA_VALIDATION_ENABLED": "False",
        "PANDERA_VALIDATION_DEPTH": "SCHEMA_ONLY",
    }
    result = subprocess.run(  # fixed argv: the current interpreter and a literal script
        [sys.executable, "-c", _ENV_SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ft_result_consistent"


def test_find_violations_missing_column_raises_contract_schema_error() -> None:
    frame = parse_rows([_row("valid_full")]).drop("ft_home_goals")
    with pytest.raises(ContractSchemaError, match="ft_home_goals"):
        find_violations(frame, CURRENT_CONTRACT, RUN_DATE)
    with pytest.raises(ContractSchemaError, match="source_row_number"):
        find_violations(
            parse_rows([_row("valid_full")]).drop("source_row_number"), CURRENT_CONTRACT, RUN_DATE
        )
