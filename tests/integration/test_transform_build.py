"""Build mechanics of the dbt project: nodes, schemas, tests, failure and recovery (feature 003)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import psycopg
import yaml

from tests.conftest import fixture_path
from tests.integration import helpers
from tests.integration.conftest import TransformedDb
from tests.integration.dbt_runner import TRANSFORM_DIR, dbt_env, run_dbt
from tests.stages import CONTRACT_STARTED_AT

PROJECT = "football_analytics"

#: The models the project must contain (data-model.md §2-§3). Extended per story.
EXPECTED_MODELS: frozenset[str] = frozenset({"stg_leagues", "stg_teams", "stg_matches"})

#: Model -> key column sets. A one-column key needs unique + not_null; a composite key needs
#: unique_combination_of_columns + not_null on each column (data-model.md §2-§3).
EXPECTED_KEYS: dict[str, list[tuple[str, ...]]] = {
    "stg_leagues": [("league_code",)],
    "stg_teams": [("team_id",), ("team_key",)],
    "stg_matches": [("match_id",)],
}

#: Model -> {column: (referenced model, referenced field)} (data-model.md §2-§3).
EXPECTED_FOREIGN_KEYS: dict[str, dict[str, tuple[str, str]]] = {
    "stg_leagues": {},
    "stg_teams": {},
    "stg_matches": {
        "league_code": ("stg_leagues", "league_code"),
        "home_team_id": ("stg_teams", "team_id"),
        "away_team_id": ("stg_teams", "team_id"),
    },
}

STAGING_MODELS = ("stg_leagues", "stg_teams", "stg_matches")


def _model_ids(statuses: dict[str, str]) -> set[str]:
    prefix = f"model.{PROJECT}."
    return {uid.removeprefix(prefix) for uid in statuses if uid.startswith(prefix)}


def _manifest_nodes(manifest: dict[str, object], resource_type: str) -> list[dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = manifest["nodes"]  # type: ignore[assignment]  # JSON shape
    return [n for n in nodes.values() if n["resource_type"] == resource_type]


def test_build_succeeds_and_runs_every_node(transformed_db: TransformedDb) -> None:
    """One build creates every model and runs every test; nothing is deprecated."""
    build = transformed_db.build
    assert build.returncode == 0, build.stdout
    statuses = build.statuses()
    assert statuses
    assert set(statuses.values()) <= {"success", "pass"}
    assert _model_ids(statuses) == EXPECTED_MODELS
    assert {p.stem for p in (TRANSFORM_DIR / "models").rglob("*.sql")} == EXPECTED_MODELS
    manifest = build.manifest()
    singular_run = {
        uid.split(".")[2]
        for uid in statuses
        if uid.startswith(f"test.{PROJECT}.") and uid.count(".") == 2
    }
    singular_files = {p.stem for p in (TRANSFORM_DIR / "tests").glob("*.sql")}
    assert singular_run == singular_files
    tested = {
        str(n["attached_node"]).removeprefix(f"model.{PROJECT}.")
        for n in _manifest_nodes(manifest, "test")
        if n.get("attached_node")
    }
    assert tested >= EXPECTED_MODELS
    assert "deprecat" not in build.stdout.lower()


def test_models_live_in_analytics_schemas(transformed_db: TransformedDb) -> None:
    """Staging views and mart tables sit in their own schemas; nothing leaks elsewhere."""
    with psycopg.connect(transformed_db.url) as conn:
        rows = conn.execute(
            b"SELECT table_schema, table_name, table_type FROM information_schema.tables "
            b"WHERE left(table_name, 4) = 'stg_' OR left(table_name, 5) = 'mart_'"
        ).fetchall()
        schemas = conn.execute(b"SELECT nspname FROM pg_namespace").fetchall()
    found = {(schema, name): kind for schema, name, kind in rows}
    expected = {
        ("analytics_staging" if name.startswith("stg_") else "analytics_marts", name): (
            "VIEW" if name.startswith("stg_") else "BASE TABLE"
        )
        for name in EXPECTED_MODELS
    }
    assert found == expected
    assert ("analytics",) not in schemas


def test_manifest_has_no_hooks_and_only_analytics_schemas(transformed_db: TransformedDb) -> None:
    """Nothing writes outside the analytics schemas; only ``public`` is read (R4)."""
    manifest = transformed_db.build.manifest()
    models = _manifest_nodes(manifest, "model")
    assert models
    for node in models:
        assert node["schema"] in {"analytics_staging", "analytics_marts"}, node["name"]
        assert node["config"]["pre-hook"] == [], node["name"]
        assert node["config"]["post-hook"] == [], node["name"]
    node_ids: dict[str, object] = manifest["nodes"]  # type: ignore[assignment]  # JSON shape
    assert not [uid for uid in node_ids if uid.startswith("operation.")]
    sources: dict[str, dict[str, Any]] = manifest["sources"]  # type: ignore[assignment]
    assert sources
    assert {s["schema"] for s in sources.values()} == {"public"}


def test_every_model_has_key_and_relationship_tests(transformed_db: TransformedDb) -> None:
    """Each model has its key tests and each foreign key a relationships test (FR-006)."""
    assert set(EXPECTED_KEYS) == EXPECTED_MODELS == set(EXPECTED_FOREIGN_KEYS)
    found: dict[str, set[tuple[str, tuple[str, ...], str | None]]] = {
        m: set() for m in EXPECTED_MODELS
    }
    for node in _manifest_nodes(transformed_db.build.manifest(), "test"):
        metadata = node.get("test_metadata")
        attached = node.get("attached_node")
        if not metadata or not attached:
            continue
        model = str(attached).removeprefix(f"model.{PROJECT}.")
        kwargs = metadata["kwargs"]
        name = metadata["name"]
        if name == "unique_combination_of_columns":
            found[model].add((name, tuple(kwargs["combination_of_columns"]), None))
        elif name == "relationships":
            ref = re.search(r"ref\('(\w+)'\)", kwargs["to"])
            assert ref is not None, kwargs
            found[model].add((name, (kwargs["column_name"],), f"{ref.group(1)}.{kwargs['field']}"))
        else:
            found[model].add((name, (kwargs.get("column_name", ""),), None))
    for model in EXPECTED_MODELS:
        for key in EXPECTED_KEYS[model]:
            if len(key) == 1:
                assert ("unique", key, None) in found[model], (model, key)
            else:
                assert ("unique_combination_of_columns", key, None) in found[model], (model, key)
            for column in key:
                assert ("not_null", (column,), None) in found[model], (model, column)
        for column, (target, field) in EXPECTED_FOREIGN_KEYS[model].items():
            assert ("relationships", (column,), f"{target}.{field}") in found[model], (
                model,
                column,
            )


def test_public_tables_unchanged_by_build(transformed_db: TransformedDb) -> None:
    """The build leaves every row and catalog entry of ``public`` (and alembic) as it was."""
    assert transformed_db.build.results, "the build did not run"
    assert transformed_db.public_before == transformed_db.public_after


def test_build_without_credentials_exits_2(tmp_path: Path) -> None:
    """A missing profile variable fails loudly with exit code 2, without a database."""
    env = dbt_env("postgresql://u:p@127.0.0.1:1/none", tmp_path / "home", tmp_path / "dbt")
    del env["DBT_ENV_SECRET_PG_PASSWORD"]
    result = run_dbt(["build"], env)
    assert result.returncode == 2, result.stdout
    assert PROJECT in result.stdout


def _fresh_ingested_db(db_url: str, tmp_path: Path, fixture: str) -> None:
    helpers.ingest_fixture(db_url, tmp_path / "raw", fixture_path(fixture), CONTRACT_STARTED_AT)


def test_build_fails_and_names_failing_test(
    db_url: str, tmp_path: Path, transformed_db: TransformedDb
) -> None:
    """A failing data test gives exit 1, names the test and skips downstream; a rerun recovers."""
    _fresh_ingested_db(db_url, tmp_path, "matches_transform.csv")
    with psycopg.connect(db_url) as conn:
        p0 = helpers.public_state(conn)
    home = tmp_path / "home"

    # (0) one successful build first, so recovery starts from a database that already holds models
    first = run_dbt(["build"], dbt_env(db_url, home, tmp_path / "dbt_first"))
    assert first.returncode == 0, first.stdout

    # (1) a copy of the project with one deliberately failing test on stg_matches.home_points
    copy = tmp_path / "transform"
    shutil.copytree(TRANSFORM_DIR, copy, ignore=shutil.ignore_patterns("target", "logs"))
    staging_yml = copy / "models" / "staging" / "_staging.yml"
    document = yaml.safe_load(staging_yml.read_text("utf-8"))
    (model,) = [m for m in document["models"] if m["name"] == "stg_matches"]
    (column,) = [c for c in model["columns"] if c["name"] == "home_points"]
    column["data_tests"].append("unique")
    staging_yml.write_text(yaml.safe_dump(document, sort_keys=False), "utf-8")
    failed = run_dbt(["build"], dbt_env(db_url, home, tmp_path / "dbt_fail"), project_dir=copy)
    assert failed.returncode == 1, failed.stdout
    assert "FAIL" in failed.stdout
    assert "unique_stg_matches_home_points" in failed.stdout
    statuses = failed.statuses()
    failing = [uid for uid in statuses if ".unique_stg_matches_home_points." in uid]
    assert [statuses[uid] for uid in failing] == ["fail"]
    downstream = [m for m in EXPECTED_MODELS if m.startswith("mart_")]
    for name in downstream:
        assert statuses[f"model.{PROJECT}.{name}"] == "skipped", name

    # (2) recovery on the same database with the unmodified project
    rerun = run_dbt(["build"], dbt_env(db_url, home, tmp_path / "dbt_rerun"))
    assert rerun.returncode == 0, rerun.stdout
    with psycopg.connect(db_url) as conn, psycopg.connect(transformed_db.url) as reference:
        assert helpers.analytics_snapshot(conn, STAGING_MODELS) == helpers.analytics_snapshot(
            reference, STAGING_MODELS
        )
        assert helpers.public_state(conn) == p0


def test_build_on_empty_database_succeeds(empty_db_url: str, tmp_path: Path) -> None:
    """With no ingested data the build exits 0 and every model is empty (spec edge case)."""
    helpers.upgrade(empty_db_url)
    result = run_dbt(["build"], dbt_env(empty_db_url, tmp_path / "home", tmp_path / "dbt"))
    assert result.returncode == 0, result.stdout
    with psycopg.connect(empty_db_url) as conn:
        for name, relation in helpers.ANALYTICS_MODELS.items():
            exists = conn.execute(b"SELECT to_regclass(%s)", (relation,)).fetchone()
            assert exists is not None
            if exists[0] is None:
                continue
            count = conn.execute(f"SELECT count(*) FROM {relation}".encode()).fetchone()
            assert count == (0,), name


def test_build_succeeds_on_real_sample(db_url: str, tmp_path: Path) -> None:
    """Real source rows go through the whole project and pass every test (SC-003 proxy)."""
    _fresh_ingested_db(db_url, tmp_path, "matches_real_sample.csv")
    result = run_dbt(["build"], dbt_env(db_url, tmp_path / "home", tmp_path / "dbt"))
    assert result.returncode == 0, result.stdout
    assert result.statuses()
    assert set(result.statuses().values()) <= {"success", "pass"}
