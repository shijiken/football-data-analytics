"""Unit test of the pure environment builder used to run dbt in integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.dbt_runner import dbt_env


def test_dbt_env_parses_url_and_passes_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The URL is decoded into the five profile variables; the shell leaks nothing."""
    monkeypatch.setenv("DBT_PROFILES_DIR", "/elsewhere")
    monkeypatch.setenv("PGPASSWORD", "leak")
    env = dbt_env(
        "postgresql://us%40er:p%3Ass@db.example:6543/name", home=tmp_path, target_dir=tmp_path
    )
    assert set(env) == {
        "PATH",
        "HOME",
        "DBT_PG_HOST",
        "DBT_PG_PORT",
        "DBT_PG_USER",
        "DBT_ENV_SECRET_PG_PASSWORD",
        "DBT_PG_DBNAME",
        "DBT_TARGET_PATH",
        "DBT_LOG_PATH",
        "DBT_SEND_ANONYMOUS_USAGE_STATS",
    }
    assert env["DBT_PG_HOST"] == "db.example"
    assert env["DBT_PG_PORT"] == "6543"
    assert env["DBT_PG_USER"] == "us@er"
    assert env["DBT_ENV_SECRET_PG_PASSWORD"] == "p:ss"
    assert env["DBT_PG_DBNAME"] == "name"
    assert env["HOME"] == str(tmp_path)
    assert env["DBT_TARGET_PATH"] == str(tmp_path / "target")
    assert env["DBT_LOG_PATH"] == str(tmp_path / "logs")
    assert env["DBT_SEND_ANONYMOUS_USAGE_STATS"] == "false"
