"""Run dbt as a subprocess for integration tests (specs/003-dbt-transforms/research.md R5).

No dbt import: the tests run exactly the command that the container and the README run, with an
explicit environment so nothing leaks from the developer's shell or ``~/.dbt``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from tests.integration.helpers import REPO_ROOT

logger = logging.getLogger(__name__)

TRANSFORM_DIR = REPO_ROOT / "transform"
_PROJECT = "football_analytics"


@dataclass(frozen=True)
class DbtResult:
    """Outcome of one dbt invocation.

    Attributes:
        returncode: The process exit code.
        stdout: Standard output followed by standard error.
        results: The ``results`` list of ``run_results.json`` (empty if the file is missing).
        target_dir: The directory ``DBT_TARGET_PATH`` pointed to (holds the JSON artifacts).
    """

    returncode: int
    stdout: str
    results: list[dict[str, object]]
    target_dir: Path

    def statuses(self) -> dict[str, str]:
        """Return ``unique_id -> status`` for every node in ``run_results.json``."""
        return {str(r["unique_id"]): str(r["status"]) for r in self.results}

    def compiled_sql(self, test_name: str) -> str:
        """Return the raw compiled SQL of the test node called ``test_name``.

        Callers wrap it as ``f"SELECT count(*) FROM (\\n{sql}\\n) t"``; the newlines keep a trailing
        ``--`` comment from swallowing the closing parenthesis.

        Raises:
            KeyError: If no such node ran.
        """
        wanted = f"test.{_PROJECT}.{test_name}"
        for result in self.results:
            unique_id = str(result["unique_id"])
            if unique_id == wanted or unique_id.startswith(wanted + "."):
                return str(result["compiled_code"])
        raise KeyError(test_name)

    def manifest(self) -> dict[str, object]:
        """Return the parsed ``manifest.json`` of the run."""
        document: dict[str, object] = json.loads(
            (self.target_dir / "manifest.json").read_text("utf-8")
        )
        return document


def dbt_env(database_url: str, home: Path, target_dir: Path) -> dict[str, str]:
    """Build the complete environment for a dbt subprocess from a libpq URL.

    Args:
        database_url: ``postgresql://user:password@host:port/dbname`` (percent-encoded parts).
        home: Directory used as ``HOME`` (so ``~/.dbt`` is never read).
        target_dir: Directory whose ``target`` and ``logs`` subdirectories receive artifacts.

    Returns:
        Exactly the variables dbt needs; nothing else from ``os.environ`` except ``PATH``.
    """
    parts = urlsplit(database_url)
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "DBT_PG_HOST": parts.hostname or "",
        "DBT_PG_PORT": str(parts.port or 5432),
        "DBT_PG_USER": unquote(parts.username or ""),
        "DBT_ENV_SECRET_PG_PASSWORD": unquote(parts.password or ""),
        "DBT_PG_DBNAME": parts.path.lstrip("/"),
        "DBT_TARGET_PATH": str(target_dir / "target"),
        "DBT_LOG_PATH": str(target_dir / "logs"),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    }


def run_dbt(
    args: Sequence[str],
    env: Mapping[str, str],
    *,
    project_dir: Path = TRANSFORM_DIR,
    timeout: float = 300,
) -> DbtResult:
    """Run ``dbt <args>`` against ``project_dir`` and collect the result.

    The working directory is the parent of ``DBT_TARGET_PATH``, never the repository.

    Args:
        args: dbt arguments, e.g. ``["build"]``.
        env: The complete subprocess environment (see :func:`dbt_env`).
        project_dir: dbt project and profiles directory.
        timeout: Seconds before the subprocess is killed.

    Returns:
        The exit code, combined output and parsed ``run_results.json``.
    """
    target_path = Path(env["DBT_TARGET_PATH"])
    cwd = target_path.parent
    cwd.mkdir(parents=True, exist_ok=True)
    command = [
        str(Path(sys.executable).with_name("dbt")),
        *args,
        "--project-dir",
        str(project_dir),
        "--profiles-dir",
        str(project_dir),
    ]
    (target_path / "run_results.json").unlink(missing_ok=True)  # never read a stale run
    logger.debug("running %s in %s", " ".join(command), cwd)
    completed = subprocess.run(
        command,
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        cwd=cwd,
    )
    results_path = target_path / "run_results.json"
    results: list[dict[str, object]] = []
    if results_path.exists():
        results = json.loads(results_path.read_text("utf-8"))["results"]
    return DbtResult(
        returncode=completed.returncode,
        stdout=completed.stdout + completed.stderr,
        results=results,
        target_dir=target_path,
    )
