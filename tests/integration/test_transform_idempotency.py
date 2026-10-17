"""Idempotency of the transform: a second build changes nothing (feature 003, FR-007, SC-004)."""

from __future__ import annotations

from pathlib import Path

import psycopg

from tests.conftest import fixture_path
from tests.integration import helpers
from tests.integration.dbt_runner import dbt_env, run_dbt
from tests.stages import CONTRACT_STARTED_AT


def test_second_build_is_identical(db_url: str, tmp_path: Path) -> None:
    """Two builds give equal analytics contents and catalog, and ``public`` never changes."""
    helpers.ingest_fixture(
        db_url, tmp_path / "raw", fixture_path("matches_transform.csv"), CONTRACT_STARTED_AT
    )
    env = dbt_env(db_url, tmp_path / "home", tmp_path / "dbt")
    with psycopg.connect(db_url) as conn:
        p0 = helpers.public_state(conn)

    first = run_dbt(["build"], env)
    assert first.returncode == 0, first.stdout
    with psycopg.connect(db_url) as conn:
        a1 = helpers.analytics_snapshot(conn)
        c1 = helpers.analytics_catalog_snapshot(conn)
        p1 = helpers.public_state(conn)

    second = run_dbt(["build"], env)
    assert second.returncode == 0, second.stdout
    with psycopg.connect(db_url) as conn:
        a2 = helpers.analytics_snapshot(conn)
        c2 = helpers.analytics_catalog_snapshot(conn)
        p2 = helpers.public_state(conn)

    assert a1 == a2
    assert c1 == c2
    assert p0 == p1 == p2
    models_in_manifest = {
        str(n["name"])
        for n in second.manifest()["nodes"].values()  # type: ignore[attr-defined]  # JSON shape
        if n["resource_type"] == "model"
    }
    assert set(a1) == models_in_manifest
    assert all(a1[m] for m in a1), "every model has fixture rows"
