"""Extract stage: obtain the raw file, read its header and body (all I/O lives here)."""

from __future__ import annotations

import csv
import hashlib
import http.client
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from football_stats.errors import MalformedSourceError, SourceUnavailableError

logger = logging.getLogger(__name__)

_CHUNK_BYTES = 1024 * 1024
_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class RawFile:
    """A content-addressed, unmodified copy of the source file."""

    path: Path
    sha256: str
    bytes: int
    source_url: str


def read_header(path: Path) -> list[str]:
    """Return the header names exactly as written (duplicates are not renamed).

    Raises:
        MalformedSourceError: If the file is empty or not valid UTF-8.
    """
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle), None)
    except UnicodeDecodeError as exc:
        raise MalformedSourceError(f"Source file is not valid UTF-8: {exc}") from exc
    if header is None:
        raise MalformedSourceError("Source file is empty (no header line)")
    return header


def check_structure(path: Path, expected_fields: int) -> None:
    """Verify every record has 0 (empty line) or ``expected_fields`` fields.

    polars silently pads short rows with nulls, so this single stdlib-``csv`` pass is the
    guard that keeps a truncated line from loading with silent NULLs (research.md R4).

    Raises:
        MalformedSourceError: On invalid UTF-8 or a record with the wrong field count; the
            message names the 1-based data-record number (the header is not counted).
    """
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            next(reader, None)  # header
            for record_number, fields in enumerate(reader, start=1):
                if len(fields) not in (0, expected_fields):
                    raise MalformedSourceError(
                        f"record {record_number} has {len(fields)} fields, "
                        f"expected {expected_fields}"
                    )
    except UnicodeDecodeError as exc:
        raise MalformedSourceError(f"Source file is not valid UTF-8: {exc}") from exc
    except csv.Error as exc:
        raise MalformedSourceError(f"Source file is not valid CSV: {exc}") from exc


def read_source(path: Path) -> pl.DataFrame:
    """Read the body with every column as a string and add ``source_row_number`` (from 1).

    An empty line is one all-null record (pinned by a unit test). A header-only file gives
    an empty frame that still has every column.

    Raises:
        MalformedSourceError: If polars cannot parse the file.
    """
    try:
        frame = pl.read_csv(path, infer_schema=False, encoding="utf8")
    except (
        pl.exceptions.ComputeError,
        pl.exceptions.NoDataError,
        UnicodeDecodeError,
    ) as exc:
        raise MalformedSourceError(f"Source file could not be parsed: {exc}") from exc
    return frame.with_columns(
        pl.int_range(1, frame.height + 1, dtype=pl.UInt32).alias("source_row_number")
    )


def fetch_to_raw(source: str | Path, raw_dir: Path) -> RawFile:
    """Copy or download ``source`` into ``raw_dir/matches-<sha256>.csv`` (read-only).

    The bytes are streamed to a ``.partial-<pid>`` file while hashing, checked against
    ``Content-Length`` when present, then atomically renamed. An existing target is reused.

    Args:
        source: A URL (``http(s)://``, ``file://``) or a local path.
        raw_dir: Directory for content-addressed raw copies; created if needed.

    Raises:
        SourceUnavailableError: If the source cannot be fetched completely.
    """
    if isinstance(source, Path):
        url = source.resolve().as_uri()
    elif "://" in source:
        url = source
    else:
        url = Path(source).resolve().as_uri()
    partial = raw_dir / f".partial-{os.getpid()}"
    try:
        raw_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with (
            urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response,
            partial.open("wb") as out,
        ):
            expected = response.headers.get("Content-Length")
            while chunk := response.read(_CHUNK_BYTES):
                digest.update(chunk)
                out.write(chunk)
                size += len(chunk)
        if expected is not None and not (expected.isascii() and expected.strip() == str(size)):
            raise SourceUnavailableError(
                f"Short or unverifiable read from {url}: got {size} bytes, "
                f"Content-Length {expected!r}"
            )
        sha = digest.hexdigest()
        target = raw_dir / f"matches-{sha}.csv"
        if target.exists():
            logger.info("raw copy already present path=%s", target)
        else:
            os.replace(partial, target)
            target.chmod(0o444)
            logger.info("raw copy stored path=%s bytes=%d sha256=%s", target, size, sha)
        return RawFile(path=target, sha256=sha, bytes=size, source_url=url)
    except (
        urllib.error.URLError,
        TimeoutError,
        http.client.IncompleteRead,
        OSError,  # also covers FileNotFoundError, ConnectionError, disk full, permissions
    ) as exc:
        raise SourceUnavailableError(f"Cannot fetch {url}: {exc}") from exc
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError as exc:  # best-effort cleanup must not mask the original error
            logger.debug("could not remove %s: %s", partial, exc)
