"""Static checks of the dbt project, its packaging and its container wiring (feature 003).

These tests only read files: no subprocess, no network, no database.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml
from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parents[2]
TRANSFORM = REPO_ROOT / "transform"

_ENV_CALL = re.compile(r"^\{\{\s*env_var\('([A-Z0-9_]+)'\)\s*(\|\s*as_number\s*)?\}\}$")
_VOLATILE = re.compile(
    r"\b(now|random|nextval|clock_timestamp|statement_timestamp)\s*\("
    r"|\bcurrent_(date|timestamp|time)\b|pre[-_]hook|post[-_]hook",
    re.IGNORECASE,
)


def _yaml(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load(path.read_text("utf-8"))
    return document


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))


def _profile_output() -> dict[str, Any]:
    profile = _yaml(TRANSFORM / "profiles.yml")["football_analytics"]
    output: dict[str, Any] = profile["outputs"][profile["target"]]
    return output


def test_dbt_pinned_to_1_11() -> None:
    """dbt 1.12 downloads an un-hashed binary at install time (research.md R1)."""
    project = _pyproject()
    requirements = {r.name: r for r in map(Requirement, project["dependency-groups"]["transform"])}
    for name in ("dbt-core", "dbt-postgres"):
        assert name in requirements
        spec = requirements[name].specifier
        assert "1.12.0" not in spec
        assert "1.11.99" in spec
    default_groups = project["tool"]["uv"]["default-groups"]
    assert "transform" in default_groups
    assert "dev" in default_groups


def test_profile_reads_only_env_vars() -> None:
    """Every connection field is one env_var() call with no default (FR-008)."""
    profile = _yaml(TRANSFORM / "profiles.yml")["football_analytics"]
    assert profile["target"] == "default"
    output = profile["outputs"]["default"]
    assert output["type"] == "postgres"
    assert output["schema"] == "analytics"
    expected = {
        "host": ("DBT_PG_HOST", False),
        "port": ("DBT_PG_PORT", True),
        "user": ("DBT_PG_USER", False),
        "password": ("DBT_ENV_SECRET_PG_PASSWORD", False),
        "dbname": ("DBT_PG_DBNAME", False),
    }
    for key, (variable, numeric) in expected.items():
        match = _ENV_CALL.match(str(output[key]))
        assert match is not None, f"{key} must be a single env_var() call: {output[key]!r}"
        assert match.group(1) == variable
        assert bool(match.group(2)) is numeric
    literal_keys = set(output) - {"type", "schema", "threads", *expected}
    assert not literal_keys, f"unexpected profile keys: {literal_keys}"


def test_project_is_offline_and_deterministic() -> None:
    """Tracking off, no partial parse, no hooks, no packages (research.md R6)."""
    project = _yaml(TRANSFORM / "dbt_project.yml")
    flags = project["flags"]
    assert flags["send_anonymous_usage_stats"] is False
    assert flags["partial_parse"] is False
    assert flags["use_colors"] is False
    assert project["profile"] == "football_analytics"
    assert "on-run-start" not in project
    assert "on-run-end" not in project
    assert not (TRANSFORM / "packages.yml").exists()
    assert not (TRANSFORM / "dependencies.yml").exists()


def test_models_layered_staging_and_marts() -> None:
    """Views in staging, tables in marts, one prefix per layer (FR-001)."""
    models = _yaml(TRANSFORM / "dbt_project.yml")["models"]["football_analytics"]
    assert models["staging"]["+materialized"] == "view"
    assert models["staging"]["+schema"] == "staging"
    assert models["marts"]["+materialized"] == "table"
    assert models["marts"]["+schema"] == "marts"
    for path in (TRANSFORM / "models").rglob("*.sql"):
        layer = path.relative_to(TRANSFORM / "models").parts[0]
        prefix = {"staging": "stg_", "marts": "mart_"}[layer]
        assert path.name.startswith(prefix), path


def test_models_use_no_volatile_functions() -> None:
    """No clock, randomness, sequences or hooks; singular tests contain no ';'."""
    for folder in ("models", "tests", "macros"):
        for path in (TRANSFORM / folder).rglob("*"):
            if path.suffix not in {".sql", ".yml"}:
                continue
            assert not _VOLATILE.search(path.read_text("utf-8")), path
    for path in (TRANSFORM / "tests").glob("*.sql"):
        assert ";" not in path.read_text("utf-8"), path
