import datetime as dt

import psycopg
import pytest
from psycopg import errors
from sqlalchemy.exc import DBAPIError

from football_stats import pipeline
from football_stats.config import Settings
from football_stats.data_contract import CURRENT_CONTRACT
from football_stats.source_contract import COLUMN_SPECS, Parser
from tests.conftest import fixture_path
from tests.contract_cases import CONTRACT_CASES, materialize
from tests.integration import helpers
from tests.stages import CONTRACT_STARTED_AT, RUN_DATE

TABLES = {"leagues", "teams", "matches", "ingest_runs", "excluded_rows"}

PG_TYPES = {
    Parser.LEAGUE: "text",
    Parser.DATE: "date",
    Parser.TIME: "time without time zone",
    Parser.COUNT: "smallint",
    Parser.DECIMAL: "double precision",
    Parser.RESULT: "character",
}


def _constraint_defs(conn: psycopg.Connection, contype: str) -> set[tuple[str, str]]:
    rows = conn.execute(
        "SELECT c.relname, pg_get_constraintdef(k.oid) FROM pg_constraint k "
        "JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND k.contype = %s",
        (contype,),
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def test_upgrade_creates_schema(empty_db_url: str) -> None:
    helpers.upgrade(empty_db_url)
    with psycopg.connect(empty_db_url) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r'"
            ).fetchall()
        }
        assert tables == TABLES | {"alembic_version"}

        assert _constraint_defs(conn, "p") >= {
            ("leagues", "PRIMARY KEY (league_code)"),
            ("teams", "PRIMARY KEY (team_id)"),
            ("matches", "PRIMARY KEY (match_id)"),
            ("ingest_runs", "PRIMARY KEY (run_id)"),
            ("excluded_rows", "PRIMARY KEY (excluded_row_id)"),
        }
        assert _constraint_defs(conn, "f") == {
            ("matches", "FOREIGN KEY (league_code) REFERENCES leagues(league_code)"),
            ("matches", "FOREIGN KEY (home_team_id) REFERENCES teams(team_id)"),
            ("matches", "FOREIGN KEY (away_team_id) REFERENCES teams(team_id)"),
            ("excluded_rows", "FOREIGN KEY (run_id) REFERENCES ingest_runs(run_id)"),
        }
        uniques = _constraint_defs(conn, "u")
        assert ("teams", "UNIQUE (team_key)") in uniques
        assert (
            "matches",
            "UNIQUE (league_code, match_date, home_team_id, away_team_id)",
        ) in uniques
        assert ("excluded_rows", "UNIQUE (source_sha256, source_row_number)") in uniques
        names = {
            r[0]
            for r in conn.execute(
                "SELECT conname FROM pg_constraint WHERE contype = 'u'"
            ).fetchall()
        }
        assert {"matches_natural_key", "excluded_rows_source_key"} <= names

        indexes = {r[0] for r in conn.execute("SELECT indexname FROM pg_indexes").fetchall()}
        assert {
            "matches_home_team_id_idx",
            "matches_away_team_id_idx",
            "excluded_rows_run_id_idx",
        } <= indexes

        run_columns = {
            r[0]: (r[1], r[2], r[3])
            for r in conn.execute(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'ingest_runs' "
                "AND column_name IN ('contract_version', 'violations_by_rule', "
                "'matches_removed', 'excluded_rows_removed')"
            ).fetchall()
        }
        assert run_columns == {
            "contract_version": ("integer", "NO", None),
            "violations_by_rule": ("jsonb", "NO", None),
            "matches_removed": ("integer", "NO", None),
            "excluded_rows_removed": ("integer", "NO", None),
        }
        reason_check = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'excluded_rows_reason_check'"
        ).fetchone()
        assert reason_check is not None
        assert "'contract_violation'" in reason_check[0]

        contract_constraints = conn.execute(
            "SELECT conname, contype, convalidated, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'matches'::regclass AND conname LIKE 'matches_contract_%'"
        ).fetchall()
        assert {r[0] for r in contract_constraints} == {
            f"matches_contract_{rule_id}" for rule_id in CURRENT_CONTRACT.rule_ids
        }
        for _name, contype, validated, definition in contract_constraints:
            assert contype == "c"
            assert validated is False
            assert definition.endswith("NOT VALID")


def test_upgrade_backfills_existing_runs(empty_db_url: str) -> None:
    helpers.upgrade(empty_db_url, "0001_initial_schema")
    with psycopg.connect(empty_db_url) as conn:
        conn.execute(
            "INSERT INTO ingest_runs (started_at, finished_at, source_url, source_sha256, "
            "source_bytes, raw_path, rows_read, rows_loaded, rows_excluded, excluded_by_reason, "
            "matches_inserted, matches_updated, matches_unchanged) VALUES (now(), now(), "
            "'file:///x.csv', %s, 1, '/x', 3, 2, 1, '{}'::jsonb, 2, 0, 0)",
            ("a" * 64,),
        )
        conn.commit()
    helpers.upgrade(empty_db_url)
    with psycopg.connect(empty_db_url) as conn:
        row = conn.execute(
            "SELECT contract_version, violations_by_rule, matches_removed, "
            "excluded_rows_removed FROM ingest_runs"
        ).fetchall()
    assert row == [(0, {}, 0, 0)]


def test_upgrade_is_noop_at_head(empty_db_url: str) -> None:
    helpers.upgrade(empty_db_url)
    with psycopg.connect(empty_db_url) as conn:
        before = helpers.catalog_snapshot(conn)
        version_before = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    helpers.upgrade(empty_db_url)
    with psycopg.connect(empty_db_url) as conn:
        assert helpers.catalog_snapshot(conn) == before
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == version_before
    assert version_before == [("0003_contract_constraints",)]


def test_downgrade_then_upgrade_roundtrip(empty_db_url: str) -> None:
    helpers.upgrade(empty_db_url)
    with psycopg.connect(empty_db_url) as conn:
        before = helpers.catalog_snapshot(conn)
    helpers.downgrade(empty_db_url, "base")
    with psycopg.connect(empty_db_url) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r'"
            ).fetchall()
        }
        assert tables == {"alembic_version"}
    helpers.upgrade(empty_db_url)
    with psycopg.connect(empty_db_url) as conn:
        assert helpers.catalog_snapshot(conn) == before


def test_source_contract_matches_db_columns(db_conn: psycopg.Connection) -> None:
    rows = db_conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'matches'"
    ).fetchall()
    db_types = {r[0]: r[1] for r in rows}

    expected: dict[str, str] = {}
    for spec in COLUMN_SPECS:
        if spec.parser is Parser.TEAM:
            continue  # team names become home_team_id / away_team_id
        expected[spec.db_column] = PG_TYPES[spec.parser]
    expected["home_team_id"] = "integer"
    expected["away_team_id"] = "integer"
    expected["source_sha256"] = "character"
    expected["source_row_number"] = "integer"
    expected["match_id"] = "bigint"
    assert db_types == expected


def test_every_contract_rule_has_db_constraint(db_conn: psycopg.Connection) -> None:
    names = {
        r[0].removeprefix("matches_contract_")
        for r in db_conn.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'matches'::regclass "
            "AND conname LIKE 'matches_contract_%'"
        )
    }
    assert names == set(CURRENT_CONTRACT.rule_ids)


def test_upgrade_succeeds_with_violating_matches(empty_db_url: str) -> None:
    helpers.seed_pre_contract(empty_db_url, fixture_path("matches_contract.csv"), RUN_DATE)
    helpers.upgrade(empty_db_url)
    with psycopg.connect(empty_db_url) as conn:
        numbers = [r[0] for r in conn.execute("SELECT source_row_number FROM matches ORDER BY 1")]
        assert len(numbers) == 18
        assert set(range(2, 14)) | {22} <= set(numbers)

        case = next(c for c in CONTRACT_CASES if c.case_id == "ht_home_gt_ft")
        row = materialize(case, RUN_DATE)
        assert row["HTHome"] == "3.0"
        with pytest.raises(errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO matches (league_code, match_date, home_team_id, away_team_id, "
                "ft_home_goals, ft_away_goals, ft_result, ht_home_goals, source_sha256, "
                "source_row_number) VALUES ('E0', %s, (SELECT min(team_id) FROM teams), "
                "(SELECT max(team_id) FROM teams), 2, 1, 'H', 3, %s, 999)",
                (dt.date(2019, 1, 1), "b" * 64),
            )
        before = helpers.catalog_snapshot(conn)
        version = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    helpers.upgrade(empty_db_url)
    with psycopg.connect(empty_db_url) as conn:
        assert helpers.catalog_snapshot(conn) == before
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == version


def test_downgrade_refused_with_contract_violation_rows(db_url: str, settings: Settings) -> None:
    pipeline.run(
        settings, source_file=fixture_path("matches_contract.csv"), started_at=CONTRACT_STARTED_AT
    )
    with pytest.raises(DBAPIError, match="excluded_rows_reason_check"):
        helpers.downgrade(db_url, "0001_initial_schema")
    with psycopg.connect(db_url) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [
            ("0002_contract_run_metadata",)
        ]
        assert conn.execute(
            "SELECT count(*) FROM pg_constraint WHERE conname LIKE 'matches_contract_%'"
        ).fetchone() == (0,)
        columns = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'ingest_runs'"
            )
        }
        assert {
            "contract_version",
            "violations_by_rule",
            "matches_removed",
            "excluded_rows_removed",
        } <= columns
        reason_check = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'excluded_rows_reason_check'"
        ).fetchone()
        assert reason_check is not None
        assert "'contract_violation'" in reason_check[0]
        assert conn.execute(
            "SELECT count(*) FROM excluded_rows WHERE reason = 'contract_violation'"
        ).fetchone() == (15,)
