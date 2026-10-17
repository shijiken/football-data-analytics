"""Build mechanics of the dbt project: nodes, schemas, tests, failure and recovery (feature 003)."""

from __future__ import annotations

import io
import re
import shutil
import tarfile
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from testcontainers.postgres import PostgresContainer

from tests.conftest import fixture_path
from tests.integration import helpers
from tests.integration.conftest import TransformedDb
from tests.integration.dbt_runner import TRANSFORM_DIR, dbt_env, run_dbt
from tests.stages import CONTRACT_STARTED_AT

PROJECT = "football_analytics"

#: The models the project must contain (data-model.md §2-§3). Extended per story.
EXPECTED_MODELS: frozenset[str] = frozenset(
    {
        "stg_leagues",
        "stg_teams",
        "stg_matches",
        "mart_team_form",
        "mart_league_season_home_advantage",
        "mart_league_season_goals",
        "mart_team_ratings",
    }
)

#: Model -> key column sets. A one-column key needs unique + not_null; a composite key needs
#: unique_combination_of_columns + not_null on each column (data-model.md §2-§3).
EXPECTED_KEYS: dict[str, list[tuple[str, ...]]] = {
    "stg_leagues": [("league_code",)],
    "stg_teams": [("team_id",), ("team_key",)],
    "stg_matches": [("match_id",)],
    "mart_team_form": [("match_id", "team_id")],
    "mart_league_season_home_advantage": [("league_code", "season")],
    "mart_league_season_goals": [("league_code", "season")],
    "mart_team_ratings": [("team_id",)],
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
    "mart_team_form": {
        "match_id": ("stg_matches", "match_id"),
        "team_id": ("stg_teams", "team_id"),
        "opponent_team_id": ("stg_teams", "team_id"),
    },
    "mart_league_season_home_advantage": {"league_code": ("stg_leagues", "league_code")},
    "mart_league_season_goals": {"league_code": ("stg_leagues", "league_code")},
    "mart_team_ratings": {"team_id": ("stg_teams", "team_id")},
}


def _model_ids(statuses: dict[str, str]) -> set[str]:
    prefix = f"model.{PROJECT}."
    return {uid.removeprefix(prefix) for uid in statuses if uid.startswith(prefix)}


def _manifest_nodes(manifest: dict[str, Any], resource_type: str) -> list[dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = manifest["nodes"]
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
    assert not [uid for uid in manifest["nodes"] if uid.startswith("operation.")]
    sources: dict[str, dict[str, Any]] = manifest["sources"]
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

    # (2) put the marts into a genuinely partial state that only a full rebuild repairs
    with psycopg.connect(db_url) as conn, psycopg.connect(transformed_db.url) as reference:
        wanted = helpers.analytics_snapshot(reference)
        with conn.transaction():
            conn.execute(b"DELETE FROM analytics_marts.mart_team_form WHERE team_match_number > 2")
            conn.execute(b"UPDATE analytics_marts.mart_team_ratings SET peak_elo = 0")
            conn.execute(b"DROP TABLE analytics_marts.mart_league_season_goals")
        with pytest.raises(psycopg.errors.UndefinedTable):
            helpers.analytics_snapshot(conn)
        conn.rollback()
        damaged = helpers.analytics_snapshot(conn, ("mart_team_form", "mart_team_ratings"))
        assert damaged["mart_team_form"] != wanted["mart_team_form"]
        assert damaged["mart_team_ratings"] != wanted["mart_team_ratings"]

    # (3) recovery on the same database with the unmodified project: every node runs and passes
    rerun = run_dbt(["build"], dbt_env(db_url, home, tmp_path / "dbt_rerun"))
    assert rerun.returncode == 0, rerun.stdout
    rerun_statuses = rerun.statuses()
    assert _model_ids(rerun_statuses) == EXPECTED_MODELS
    assert {s for uid, s in rerun_statuses.items() if uid.startswith("model.")} == {"success"}
    assert {s for uid, s in rerun_statuses.items() if uid.startswith("test.")} == {"pass"}
    with psycopg.connect(db_url) as conn:
        assert helpers.analytics_snapshot(conn) == wanted
        assert helpers.public_state(conn) == p0


def test_build_on_empty_database_succeeds(empty_db_url: str, tmp_path: Path) -> None:
    """With no ingested data the build exits 0 and every model is empty (spec edge case)."""
    helpers.upgrade(empty_db_url)
    result = run_dbt(["build"], dbt_env(empty_db_url, tmp_path / "home", tmp_path / "dbt"))
    assert result.returncode == 0, result.stdout
    assert set(result.statuses().values()) <= {"success", "pass"}
    with psycopg.connect(empty_db_url) as conn:
        for name, relation in helpers.ANALYTICS_MODELS.items():
            exists = conn.execute(b"SELECT to_regclass(%s)", (relation,)).fetchone()
            assert exists is not None
            assert exists[0] is not None, f"{name} was not built"
            count = conn.execute(f"SELECT count(*) FROM {relation}".encode()).fetchone()
            assert count == (0,), name


def test_build_succeeds_on_real_sample(db_url: str, tmp_path: Path) -> None:
    """Real source rows go through the whole project and pass every test (SC-003 proxy)."""
    _fresh_ingested_db(db_url, tmp_path, "matches_real_sample.csv")
    result = run_dbt(["build"], dbt_env(db_url, tmp_path / "home", tmp_path / "dbt"))
    assert result.returncode == 0, result.stdout
    assert result.statuses()
    assert set(result.statuses().values()) <= {"success", "pass"}


_INT = ("integer", 32, 0)
_BIGINT = ("bigint", 64, 0)
_TEXT = ("text", None, None)
_DATE = ("date", None, None)
_BOOL = ("boolean", None, None)
_FLOAT = ("double precision", 53, None)
_SHARE = ("numeric", 5, 4)
_AVG = ("numeric", 6, 4)

#: Mart -> ordered (column, (data_type, numeric_precision, numeric_scale)) - data-model.md §3.
EXPECTED_MART_COLUMNS: dict[str, list[tuple[str, tuple[str, int | None, int | None]]]] = {
    "mart_team_form": [
        ("match_id", _BIGINT),
        ("team_id", _INT),
        ("opponent_team_id", _INT),
        ("league_code", _TEXT),
        ("season", _TEXT),
        ("season_start_year", _INT),
        ("match_date", _DATE),
        ("is_home", _BOOL),
        ("goals_for", _INT),
        ("goals_against", _INT),
        ("result", _TEXT),
        ("points", _INT),
        ("team_match_number", _BIGINT),
        ("form_matches", _BIGINT),
        ("form_points", _BIGINT),
        ("form_goals_for", _BIGINT),
        ("form_goals_against", _BIGINT),
    ],
    "mart_league_season_home_advantage": [
        ("league_code", _TEXT),
        ("season", _TEXT),
        ("season_start_year", _INT),
        ("matches_played", _BIGINT),
        ("home_wins", _BIGINT),
        ("draws", _BIGINT),
        ("away_wins", _BIGINT),
        ("home_win_share", _SHARE),
        ("draw_share", _SHARE),
        ("away_win_share", _SHARE),
        ("avg_home_goals", _AVG),
        ("avg_away_goals", _AVG),
    ],
    "mart_league_season_goals": [
        ("league_code", _TEXT),
        ("season", _TEXT),
        ("season_start_year", _INT),
        ("matches_played", _BIGINT),
        ("total_goals", _BIGINT),
        ("goals_per_match", _AVG),
        ("over_2_5_matches", _BIGINT),
        ("over_2_5_share", _SHARE),
        ("btts_matches", _BIGINT),
        ("btts_share", _SHARE),
    ],
    "mart_team_ratings": [
        ("team_id", _INT),
        ("team_key", _TEXT),
        ("team_name", _TEXT),
        ("rated_matches", _BIGINT),
        ("latest_elo", _FLOAT),
        ("latest_elo_date", _DATE),
        ("peak_elo", _FLOAT),
        ("peak_elo_date", _DATE),
        ("lowest_elo", _FLOAT),
        ("lowest_elo_date", _DATE),
    ],
}

EXPECTED_PRIMARY_KEYS: dict[str, list[str]] = {
    "mart_team_form": ["match_id", "team_id"],
    "mart_league_season_home_advantage": ["league_code", "season"],
    "mart_league_season_goals": ["league_code", "season"],
    "mart_team_ratings": ["team_id"],
}


def test_mart_columns_match_contract(transformed_db: TransformedDb) -> None:
    """Mart columns, types and primary keys equal data-model.md §3; contracts are enforced."""
    nodes = {n["name"]: n for n in _manifest_nodes(transformed_db.build.manifest(), "model")}
    with psycopg.connect(transformed_db.url) as conn:
        for mart, expected in EXPECTED_MART_COLUMNS.items():
            rows = conn.execute(
                b"SELECT column_name, data_type, numeric_precision, numeric_scale "
                b"FROM information_schema.columns WHERE table_schema = 'analytics_marts' "
                b"AND table_name = %s ORDER BY ordinal_position",
                (mart,),
            ).fetchall()
            assert [(r[0], (r[1], r[2], r[3])) for r in rows] == expected, mart
            keys = conn.execute(
                b"SELECT a.attname FROM pg_constraint k "
                b"JOIN LATERAL unnest(k.conkey) WITH ORDINALITY AS u(attnum, ord) ON true "
                b"JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.attnum "
                b"WHERE k.contype = 'p' AND k.conrelid = %s::regclass ORDER BY u.ord",
                (f"analytics_marts.{mart}",),
            ).fetchall()
            assert [k[0] for k in keys] == EXPECTED_PRIMARY_KEYS[mart], mart
            assert nodes[mart]["config"]["contract"]["enforced"] is True, mart


def test_team_form_has_lookup_index(transformed_db: TransformedDb) -> None:
    """The dbt ``indexes`` config creates the team-form lookup index (FR-010)."""
    with psycopg.connect(transformed_db.url) as conn:
        rows = conn.execute(
            b"SELECT pg_get_indexdef(i.indexrelid) FROM pg_index i "
            b"WHERE i.indrelid = 'analytics_marts.mart_team_form'::regclass AND NOT i.indisprimary"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0].endswith("USING btree (team_id, match_date, match_id)")


def _copy_into_container(container: PostgresContainer, source: Path, directory: str) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        archive.add(source, arcname=source.name)
    buffer.seek(0)
    assert container.get_wrapped_container().put_archive(directory, buffer.getvalue())


def test_explain_script_leaves_catalog_unchanged(
    transformed_db: TransformedDb, postgres_container: PostgresContainer
) -> None:
    """The measurement script runs, prints every labelled plan, and changes no catalog entry."""
    script = TRANSFORM_DIR.parent / "scripts" / "explain_transforms.sql"
    _copy_into_container(postgres_container, script, "/tmp")  # inside the container
    with psycopg.connect(transformed_db.url) as conn:
        analytics_before = helpers.analytics_catalog_snapshot(conn)
        public_before = helpers.catalog_snapshot(conn)
    database = transformed_db.url.rsplit("/", 1)[1]
    exit_code, output = postgres_container.get_wrapped_container().exec_run(
        [
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            postgres_container.username,
            "-d",
            database,
            "-f",
            "/tmp/explain_transforms.sql",  # inside the container
        ]
    )
    text = output.decode("utf-8")
    assert exit_code == 0, text
    for label in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6"):
        assert f"{label}:" in text, label
    variants = 2 + 2 + 2 + 1 + 2 + 2
    assert text.count("Execution Time:") == 5 * variants
    with psycopg.connect(transformed_db.url) as conn:
        assert helpers.analytics_catalog_snapshot(conn) == analytics_before
        assert helpers.catalog_snapshot(conn) == public_before
