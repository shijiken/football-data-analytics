import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from jsonschema import Draft202012Validator

from football_stats import load
from football_stats.data_contract import CONTRACT_VERSION
from tests.conftest import fixture_path
from tests.integration.helpers import REPO_ROOT

SCHEMA = json.loads(
    (REPO_ROOT / "specs/002-data-contract/contracts/run-report.schema.json").read_text("utf-8")
)


def run_cli(
    args: list[str], env: dict[str, str]
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    """Run the CLI in a subprocess with an explicit environment and parse its stdout."""
    full_env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(REPO_ROOT / "src"),
        **env,
    }
    proc = subprocess.run(
        [sys.executable, "-m", "football_stats.cli", *args],
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    report = json.loads(proc.stdout)
    Draft202012Validator(SCHEMA).validate(report)
    return proc, report


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_cli_ingest_report_matches_schema(db_url: str, tmp_path: Path) -> None:
    proc, report = run_cli(
        ["ingest", "--source-file", str(fixture_path("matches_valid.csv"))],
        {"DATABASE_URL": db_url, "RAW_DATA_DIR": str(tmp_path / "raw")},
    )
    assert proc.returncode == 0, proc.stderr
    assert report["status"] == "succeeded"
    assert (report["rows_read"], report["rows_loaded"], report["rows_excluded"]) == (12, 12, 0)
    by_reason = report["excluded_by_reason"]
    by_rule = report["violations_by_rule"]
    assert isinstance(by_reason, dict)
    assert isinstance(by_rule, dict)
    assert report["rows_excluded"] == sum(by_reason.values())
    assert by_reason["contract_violation"] <= sum(by_rule.values())
    assert (by_reason["contract_violation"] == 0) == (sum(by_rule.values()) == 0)
    assert report["contract_version"] == CONTRACT_VERSION


@pytest.mark.parametrize(
    ("case", "code", "error"),
    [
        ("missing_database_url", 2, "configuration_error"),
        ("source_unreachable", 3, "source_unavailable"),
        ("missing_columns", 4, "missing_columns"),
        ("ragged_row", 4, "malformed_source"),
        ("lock_held", 5, "concurrent_run"),
        ("database_unreachable", 7, "database_error"),
    ],
)
def test_cli_exit_codes(case: str, code: int, error: str, db_url: str, tmp_path: Path) -> None:
    env = {"DATABASE_URL": db_url, "RAW_DATA_DIR": str(tmp_path / "raw")}
    valid = str(fixture_path("matches_valid.csv"))
    args = ["ingest", "--source-file", valid]
    lock: psycopg.Connection | None = None
    if case == "missing_database_url":
        del env["DATABASE_URL"]
    elif case == "source_unreachable":
        args = ["ingest"]
        env["SOURCE_URL"] = f"http://127.0.0.1:{_free_port()}/Matches.csv"
    elif case == "missing_columns":
        args = ["ingest", "--source-file", str(fixture_path("matches_missing_columns.csv"))]
    elif case == "ragged_row":
        args = ["ingest", "--source-file", str(fixture_path("matches_ragged_short.csv"))]
    elif case == "lock_held":
        lock = psycopg.connect(db_url, autocommit=True)
        lock.execute("SELECT pg_advisory_lock(%s)", (load.LOCK_KEY,))
    elif case == "database_unreachable":
        env["DATABASE_URL"] = f"postgresql://u:p@127.0.0.1:{_free_port()}/none"
    try:
        proc, report = run_cli(args, env)
    finally:
        if lock is not None:
            lock.close()
    assert proc.returncode == code, proc.stderr
    assert report["status"] == "failed"
    assert report["error"] == error
    if case == "missing_columns":
        assert "AwayTeam, FTHome" in str(report["message"])
    if case == "missing_database_url":
        assert "DATABASE_URL" in str(report["message"])


def test_cli_stdout_is_only_json(db_url: str, tmp_path: Path) -> None:
    proc, report = run_cli(
        ["ingest", "--source-file", str(fixture_path("matches_valid.csv"))],
        {"DATABASE_URL": db_url, "RAW_DATA_DIR": str(tmp_path / "raw"), "LOG_LEVEL": "INFO"},
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == report
    assert proc.stdout.count("\n") == 1
    assert "run_summary" in proc.stderr
    assert "INFO" in proc.stderr
