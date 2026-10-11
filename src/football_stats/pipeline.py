"""Pipeline orchestration: extract, clean, validate, load, report."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg

from football_stats import clean, extract, load, validate
from football_stats.config import Settings
from football_stats.errors import LoadError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunReport:
    """Result of a successful ingest run (contracts/run-report.schema.json)."""

    run_id: int
    source_url: str
    source_sha256: str
    raw_path: str
    rows_read: int
    rows_loaded: int
    rows_excluded: int
    excluded_by_reason: dict[str, int]
    matches_inserted: int
    matches_updated: int
    matches_unchanged: int
    duration_seconds: float
    status: str = "succeeded"

    def to_json(self) -> str:
        """Serialize to a single JSON document with ``status`` first."""
        fields = dataclasses.asdict(self)
        ordered = {"status": fields.pop("status"), **fields}
        return json.dumps(ordered)


def run(settings: Settings, source_file: Path | None = None) -> RunReport:
    """Run the full ingest and return its report.

    Nothing touches the database before :func:`football_stats.load.load_run`.

    Args:
        settings: Runtime configuration.
        source_file: Local file to ingest instead of downloading ``settings.source_url``.

    Raises:
        FootballStatsError: Any handled failure (see :mod:`football_stats.errors`).
    """
    started = time.monotonic()
    started_at = dt.datetime.now(dt.UTC)

    raw = extract.fetch_to_raw(
        source_file if source_file is not None else settings.source_url, settings.raw_data_dir
    )
    logger.info("stage=extract bytes=%d sha256=%s", raw.bytes, raw.sha256)

    header = extract.read_header(raw.path)
    extras = validate.check_header(header)
    if extras:
        logger.warning("source has extra columns that are ignored: %s", ", ".join(extras))
    extract.check_structure(raw.path, len(header))

    frame = extract.read_source(raw.path)
    logger.info("stage=read rows_read=%d", frame.height)
    parsed = clean.add_team_keys(clean.parse_frame(frame))
    partition = validate.partition(parsed)
    batch = clean.build_batch(partition.accepted, raw.sha256)
    logger.info(
        "stage=validate rows_read=%d rows_loaded=%d rows_excluded=%d leagues=%d teams=%d",
        partition.rows_read,
        partition.accepted.height,
        partition.excluded.height,
        batch.leagues.height,
        batch.teams.height,
    )

    meta = load.RunMeta(
        started_at=started_at,
        source_url=raw.source_url,
        source_sha256=raw.sha256,
        source_bytes=raw.bytes,
        raw_path=str(raw.path),
        rows_read=partition.rows_read,
        rows_excluded=partition.excluded.height,
        excluded_by_reason=partition.counts_by_reason(),
    )
    try:
        conn = psycopg.connect(settings.database_url)
    except psycopg.Error as exc:
        raise LoadError(f"Cannot connect to the database: {exc}") from exc
    with conn:
        counts = load.load_run(conn, batch, partition.excluded, meta)

    report = RunReport(
        run_id=counts.run_id,
        source_url=raw.source_url,
        source_sha256=raw.sha256,
        raw_path=str(raw.path),
        rows_read=partition.rows_read,
        rows_loaded=batch.matches.height,
        rows_excluded=partition.excluded.height,
        excluded_by_reason=partition.counts_by_reason(),
        matches_inserted=counts.matches_inserted,
        matches_updated=counts.matches_updated,
        matches_unchanged=counts.matches_unchanged,
        duration_seconds=round(time.monotonic() - started, 3),
    )
    logger.info("run_summary %s", report.to_json())
    return report
