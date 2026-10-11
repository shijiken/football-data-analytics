from pathlib import Path

import pytest

from football_stats.config import load_settings
from football_stats.errors import ConfigurationError
from football_stats.source_contract import PINNED_SOURCE_URL


def test_config_reads_env() -> None:
    settings = load_settings(
        {
            "DATABASE_URL": "postgresql://u:p@h:5432/d",
            "SOURCE_URL": "file:///tmp/x.csv",
            "RAW_DATA_DIR": "/tmp/raw",
            "LOG_LEVEL": "DEBUG",
        }
    )
    assert settings.database_url == "postgresql://u:p@h:5432/d"
    assert settings.source_url == "file:///tmp/x.csv"
    assert settings.raw_data_dir == Path("/tmp/raw")
    assert settings.log_level == "DEBUG"


def test_config_defaults() -> None:
    settings = load_settings({"DATABASE_URL": "postgresql://u:p@h:5432/d"})
    assert settings.source_url == PINNED_SOURCE_URL
    assert PINNED_SOURCE_URL == (
        "https://raw.githubusercontent.com/xgabora/Club-Football-Match-Data/"
        "25882a58a736daf7ece3781940eac17ae1117a66/data/Matches.csv"
    )
    assert settings.raw_data_dir == Path("data/raw")
    assert settings.log_level == "INFO"


def test_config_missing_database_url_raises() -> None:
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        load_settings({})


def test_config_invalid_log_level_raises() -> None:
    with pytest.raises(ConfigurationError, match="LOG_LEVEL"):
        load_settings({"DATABASE_URL": "postgresql://u:p@h:5432/d", "LOG_LEVEL": "LOUD"})
