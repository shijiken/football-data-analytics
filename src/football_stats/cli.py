"""Command-line entry point: ``football-stats ingest`` and ``football-stats verify``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from football_stats import pipeline
from football_stats.config import load_settings
from football_stats.errors import FootballStatsError, VerificationFailedError
from football_stats.verify import run_verify

logger = logging.getLogger("football_stats")

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="football-stats")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="download, clean, validate and load the match data")
    ingest.add_argument(
        "--source-file",
        type=Path,
        default=None,
        help="ingest this local file instead of SOURCE_URL",
    )
    verify = sub.add_parser("verify", help="compare sampled source rows with the database")
    verify.add_argument("--sample", type=int, default=20, help="rows to check; 0 = all")
    verify.add_argument("--seed", type=int, default=42, help="sampling seed")
    verify.add_argument("--source-file", type=Path, default=None, help="source to read")
    return parser


def _write_json(document: dict[str, object] | str) -> None:
    text = document if isinstance(document, str) else json.dumps(document)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _failure(error: str, message: str) -> dict[str, object]:
    return {"status": "failed", "error": error, "message": message}


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code (contracts/cli.md)."""
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, stream=sys.stderr)
    args = _build_parser().parse_args(argv)
    try:
        settings = load_settings(os.environ)
        logging.getLogger().setLevel(settings.log_level)
        if args.command == "ingest":
            _write_json(pipeline.run(settings, source_file=args.source_file).to_json())
            return 0
        _write_json(run_verify(settings, args.sample, args.seed, args.source_file).to_json())
        return 0
    except VerificationFailedError as exc:
        logger.error("%s: %s", exc.error, exc)
        document = _failure(exc.error, str(exc))
        document.update(checked=exc.checked, differences=exc.differences)
        _write_json(document)
        return exc.exit_code
    except FootballStatsError as exc:
        logger.error("%s: %s", exc.error, exc)
        _write_json(_failure(exc.error, str(exc)))
        return exc.exit_code
    except Exception as exc:
        logger.exception("unexpected error")
        _write_json(_failure("unexpected_error", f"{type(exc).__name__}: {exc}"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
