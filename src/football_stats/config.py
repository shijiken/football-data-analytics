"""Runtime configuration read from environment variables."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from football_stats.errors import ConfigurationError
from football_stats.source_contract import PINNED_SOURCE_URL

_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})


@dataclass(frozen=True)
class Settings:
    """Immutable application settings."""

    database_url: str
    source_url: str
    raw_data_dir: Path
    log_level: str


def load_settings(env: Mapping[str, str]) -> Settings:
    """Build :class:`Settings` from an explicit environment mapping.

    Args:
        env: Variable name to value, e.g. ``os.environ``.

    Returns:
        The validated settings, with defaults applied for optional variables.

    Raises:
        ConfigurationError: If ``DATABASE_URL`` is missing or ``LOG_LEVEL`` is invalid.
    """
    database_url = env.get("DATABASE_URL", "").strip()
    if not database_url:
        raise ConfigurationError("Environment variable DATABASE_URL is required")
    log_level = env.get("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in _LOG_LEVELS:
        raise ConfigurationError(
            f"Environment variable LOG_LEVEL must be one of {', '.join(sorted(_LOG_LEVELS))}"
        )
    return Settings(
        database_url=database_url,
        source_url=env.get("SOURCE_URL", "").strip() or PINNED_SOURCE_URL,
        raw_data_dir=Path(env.get("RAW_DATA_DIR", "").strip() or "data/raw"),
        log_level=log_level,
    )
