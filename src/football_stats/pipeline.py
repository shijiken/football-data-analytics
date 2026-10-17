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
from football_stats.data_contract import CURRENT_CONTRACT, DataContract
from football_stats.errors import LoadError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunReport:
    """Result of a successful ingest run (contracts/run-report.schema.json)."""

    run_id: int
    source_url: str
    source_sha256: str
    raw_path: str
    contract_version: int
    rows_read: int
    rows_loaded: int
    rows_excluded: int
    excluded_by_reason: dict[str, int]
    violations_by_rule: dict[str, int]
    matches_inserted: int
    matches_updated: int
    matches_unchanged: int
    matches_removed: int
    excluded_rows_removed: int
    duration_seconds: float
    status: str = "succeeded"

    def to_json(self) -> str:
        """Serialize to a single JSON document with ``status`` first."""
        fields = dataclasses.asdict(self)
        ordered = {"status": fields.pop("status"), **fields}
        return json.dumps(ordered)


def run(
    settings: Settings,
    source_file: Path | None = None,
    *,
    contract: DataContract = CURRENT_CONTRACT,
    started_at: dt.datetime | None = None,
) -> RunReport:
    """Run the full ingest and return its report.

    Nothing touches the database before :func:`football_stats.load.load_run`.

    Args:
        settings: Runtime configuration.
        source_file: Local file to ingest instead of downloading ``settings.source_url``.
        contract: Data contract to enforce.
        started_at: Run start instant (timezone-aware); defaults to now. Its UTC date is the
            run date used by ``date_in_range``.

    Raises:
        ValueError: If ``started_at`` is naive.
        FootballStatsError: Any handled failure (see :mod:`football_stats.errors`).
    """
    started = time.monotonic()
    if started_at is None:
        started_at = dt.datetime.now(dt.UTC)
    elif started_at.tzinfo is None:
        raise ValueError("started_at must be timezone-aware")
    run_date = started_at.astimezone(dt.UTC).date()

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
    partition = validate.partition(parsed, contract=contract, run_date=run_date)
    batch = clean.build_batch(partition.accepted, raw.sha256)
    logger.info(
        "stage=validate rows_read=%d rows_loaded=%d rows_excluded=%d leagues=%d teams=%d",
        partition.rows_read,
        partition.accepted.height,
        partition.excluded.height,
        batch.leagues.height,
        batch.teams.height,
    )

    for reason, count in partition.counts_by_reason().items():
        if count:
            logger.info("excluded reason=%s count=%d", reason, count)
    violations_by_rule = partition.violations_by_rule()
    for rule_id, count in violations_by_rule.items():
        logger.info("contract rule=%s violations=%d", rule_id, count)
    if logger.isEnabledFor(logging.DEBUG):
        for number, reason, detail in partition.excluded.select(
            "source_row_number", "reason", "detail"
        ).iter_rows():
            logger.debug("row=%d reason=%s detail=%s", number, reason, detail)

    meta = load.RunMeta(
        started_at=started_at,
        source_url=raw.source_url,
        source_sha256=raw.sha256,
        source_bytes=raw.bytes,
        raw_path=str(raw.path),
        rows_read=partition.rows_read,
        rows_excluded=partition.excluded.height,
        excluded_by_reason=partition.counts_by_reason(),
        run_date=run_date,
        contract_version=contract.version,
        violations_by_rule=violations_by_rule,
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
        contract_version=contract.version,
        rows_read=partition.rows_read,
        rows_loaded=batch.matches.height,
        rows_excluded=partition.excluded.height,
        excluded_by_reason=partition.counts_by_reason(),
        violations_by_rule=violations_by_rule,
        matches_inserted=counts.matches_inserted,
        matches_updated=counts.matches_updated,
        matches_unchanged=counts.matches_unchanged,
        matches_removed=counts.matches_removed,
        excluded_rows_removed=counts.excluded_rows_removed,
        duration_seconds=round(time.monotonic() - started, 3),
    )
    logger.info("run_summary %s", report.to_json())
    return report
