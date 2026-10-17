"""SC-004 spot check: compare source rows with the database (research.md R24)."""

from __future__ import annotations

import csv
import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from football_stats.clean import team_key
from football_stats.config import Settings
from football_stats.errors import (
    ConfigurationError,
    LoadError,
    SourceUnavailableError,
    VerificationFailedError,
)

logger = logging.getLogger(__name__)

_DbRow = tuple[str, str, str, int, int]


@dataclass(frozen=True)
class VerifyReport:
    """Successful verification (contracts/verify-report.schema.json)."""

    checked: int
    differences: list[dict[str, object]] = field(default_factory=list)
    checked_row_numbers: tuple[int, ...] = ()

    def to_json(self) -> str:
        """Serialize to the success report document."""
        return json.dumps(
            {"status": "succeeded", "checked": self.checked, "differences": self.differences}
        )


def _count_records(path: Path) -> int:
    """Count data records (an empty line is a record) without keeping them in memory."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        return sum(1 for _ in reader)


def _read_records(path: Path, wanted: set[int]) -> dict[int, dict[str, str]]:
    """Materialize only the ``wanted`` 1-based data records (empty line -> ``{}``)."""
    records: dict[int, dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        for number, fields in enumerate(reader, start=1):
            if number in wanted:
                records[number] = dict(zip(header, fields, strict=True)) if fields else {}
    return records


def _compare(number: int, source: dict[str, str], db: _DbRow | None) -> list[dict[str, object]]:
    """Return the differences between one source record and its database row."""
    if db is None:
        return [
            {"source_row_number": number, "field": "row", "source": str(number), "database": None}
        ]
    db_date, db_home, db_away, db_home_goals, db_away_goals = db
    pairs: list[tuple[str, str, str]] = [
        ("match_date", source["MatchDate"].strip(), db_date),
        ("home_team_key", team_key(source["HomeTeam"]), db_home),
        ("away_team_key", team_key(source["AwayTeam"]), db_away),
        ("ft_home_goals", str(int(float(source["FTHome"]))), str(db_home_goals)),
        ("ft_away_goals", str(int(float(source["FTAway"]))), str(db_away_goals)),
    ]
    return [
        {"source_row_number": number, "field": name, "source": want, "database": got}
        for name, want, got in pairs
        if want != got
    ]


def run_verify(
    settings: Settings, sample: int = 20, seed: int = 42, source_file: Path | None = None
) -> VerifyReport:
    """Compare seeded-random accepted source rows with the database.

    Args:
        settings: Runtime configuration.
        sample: Number of rows to check; ``0`` checks every accepted row.
        seed: Seed of the sampling ``random.Random``.
        source_file: Source to read; defaults to the raw copy of the latest run.

    Raises:
        VerificationFailedError: If any difference is found (carries the report).
        ConfigurationError: If no ingest run exists yet.
        SourceUnavailableError: If the raw file is missing.
        LoadError: If the database cannot be queried.
    """
    try:
        with psycopg.connect(settings.database_url) as conn:
            latest = conn.execute(
                "SELECT source_sha256 FROM ingest_runs ORDER BY run_id DESC LIMIT 1"
            ).fetchone()
            if latest is None:
                raise ConfigurationError("No ingest run found; run `football-stats ingest` first")
            sha = str(latest[0])
            excluded = {
                int(r[0])
                for r in conn.execute(
                    "SELECT source_row_number FROM excluded_rows WHERE source_sha256 = %s", (sha,)
                )
            }
            db_rows: dict[int, _DbRow] = {
                int(r[0]): (r[1].isoformat(), r[2], r[3], int(r[4]), int(r[5]))
                for r in conn.execute(
                    "SELECT m.source_row_number, m.match_date, h.team_key, a.team_key, "
                    "m.ft_home_goals, m.ft_away_goals FROM matches m "
                    "JOIN teams h ON h.team_id = m.home_team_id "
                    "JOIN teams a ON a.team_id = m.away_team_id WHERE m.source_sha256 = %s",
                    (sha,),
                )
            }
    except psycopg.Error as exc:
        raise LoadError(f"Cannot read the database: {exc}") from exc

    path = source_file if source_file is not None else settings.raw_data_dir / f"matches-{sha}.csv"
    try:
        total = _count_records(path)
    except FileNotFoundError as exc:
        raise SourceUnavailableError(f"Raw source file not found: {path}") from exc

    accepted = sorted(n for n in range(1, total + 1) if n not in excluded)
    chosen = (
        accepted
        if sample <= 0 or sample >= len(accepted)
        else sorted(random.Random(seed).sample(accepted, sample))
    )
    records = _read_records(path, set(chosen))
    differences: list[dict[str, object]] = []
    for number in chosen:
        differences.extend(_compare(number, records[number], db_rows.get(number)))
    logger.info("verify checked=%d differences=%d", len(chosen), len(differences))
    if differences:
        raise VerificationFailedError(
            f"{len(differences)} difference(s) in {len(chosen)} checked rows",
            len(chosen),
            differences,
        )
    return VerifyReport(checked=len(chosen), checked_row_numbers=tuple(chosen))
