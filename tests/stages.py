"""Helpers that run the pure pipeline stages on a fixture file."""

import datetime as dt
from pathlib import Path

import polars as pl
from football_stats.data_contract import CURRENT_CONTRACT, DataContract

from football_stats import clean, extract, validate
from football_stats.source_contract import EXPECTED_COLUMNS

#: Run date for pure-stage tests: after every fixture match date (latest 2022-02-07), in the past.
RUN_DATE = dt.date(2023, 6, 30)
#: Run start for integration tests that ingest ``matches_contract.csv`` or test date bounds.
CONTRACT_STARTED_AT = dt.datetime(2023, 6, 30, 12, 0, tzinfo=dt.UTC)


def parsed_frame(path: Path) -> pl.DataFrame:
    """Read ``path`` and run the parsing and team-key stages."""
    return clean.add_team_keys(clean.parse_frame(extract.read_source(path)))


def partition_file(
    path: Path, *, contract: DataContract = CURRENT_CONTRACT, run_date: dt.date = RUN_DATE
) -> validate.Partition:
    """Run read, parse, team keys and partition on ``path``."""
    return validate.partition(parsed_frame(path), contract=contract, run_date=run_date)


def partition_rows(
    rows: list[dict[str, str | None]],
    *,
    contract: DataContract = CURRENT_CONTRACT,
    run_date: dt.date = RUN_DATE,
) -> validate.Partition:
    """Partition inline rows; each row overrides a valid base row, ``{}`` = defaults.

    A value of ``None`` blanks the column; a row given as ``{"__blank__": None}`` is an
    empty line (every column null).
    """
    base: dict[str, str | None] = dict.fromkeys(EXPECTED_COLUMNS)
    base.update(
        {
            "Division": "E0",
            "MatchDate": "2020-09-12",
            "HomeTeam": "Arsenal",
            "AwayTeam": "Chelsea",
            "FTHome": "1.0",
            "FTAway": "0.0",
            "FTResult": "H",
        }
    )
    records: list[dict[str, str | None]] = []
    for overrides in rows:
        if "__blank__" in overrides:
            records.append(dict.fromkeys(EXPECTED_COLUMNS))
        else:
            records.append({**base, **overrides})
    raw = pl.DataFrame(records, schema=dict.fromkeys(EXPECTED_COLUMNS, pl.String)).with_columns(
        pl.int_range(1, len(records) + 1, dtype=pl.UInt32).alias("source_row_number")
    )
    parsed = clean.add_team_keys(clean.parse_frame(raw))
    return validate.partition(parsed, contract=contract, run_date=run_date)
