# Implementation Plan: Match Data Ingestion

**Branch**: `001-match-data-ingestion` | **Date**: 2026-10-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-match-data-ingestion/spec.md`

## Summary

Download `Matches.csv` (238,859 records, 48 columns) from a commit-pinned URL of the Club Football
Match Data repository, keep a content-addressed raw copy, check the header against the 48 expected
columns, parse every value with strict rules into typed polars columns, normalize team names,
classify each row as accepted or excluded with exactly one reason, and load leagues, teams, matches,
excluded rows and a run record into PostgreSQL **in one transaction** using COPY into temp staging
tables plus `INSERT … ON CONFLICT` upserts on natural keys. The schema is created by Alembic
migrations. A Compose stack runs `db`, `migrate` and an `ingest` job. A single script,
`scripts/check.sh`, is the deterministic quality gate (format, lint, types, unit tests, integration
tests against real PostgreSQL via testcontainers). All decisions: [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.12 (`requires-python = ">=3.12,<3.13"`, `.python-version` = 3.12)

**Primary Dependencies**: polars 1.x (cleaning/validation), psycopg[binary] 3.2.x (DB access, COPY),
alembic 1.16.x (+ SQLAlchemy 2.x as its dependency) (migrations). Stdlib only for HTTP (`urllib`),
CLI (`argparse`), config, logging. Versions locked in `uv.lock`.

**Dev dependencies**: ruff, mypy, pytest 8, testcontainers 4.x, pytest-randomly, pytest-socket,
jsonschema (validates CLI report against `contracts/run-report.schema.json`).

**Storage**: PostgreSQL 17.6 (`postgres:17.6-alpine`), same image in Compose and testcontainers.
Raw files on disk in `RAW_DATA_DIR` (content-addressed).

**Testing**: pytest; unit tests (pure functions, local fixture files, sockets disabled) and
integration tests (testcontainers PostgreSQL, loopback HTTP, CLI subprocess). Fixed random seed for
test order. No external network.

**Target Platform**: macOS (dev) and Linux containers / GitHub Actions `ubuntu-24.04` (CI).

**Project Type**: Single Python project (CLI + library), `src/` layout, plus Compose services.

**Performance Goals**: full dataset load < 5 min (SC-002); fresh checkout → loaded DB < 10 min with
≤ 2 commands (SC-001). Measured manually (research.md R25).

**Constraints**: no partial loads (single transaction, FR-012); idempotent re-runs (FR-011); every
row accounted for (FR-010, Principle I); config only via env vars; images and packages pinned.

**Scale/Scope**: ~239k rows × 48 columns (45 MB CSV), 38 leagues, 1,221 teams. Single developer.

No `NEEDS CLARIFICATION` items remain; all were resolved in research.md.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / standard | How this plan complies | Evidence (test / artifact) |
|---|---|---|
| **I. No silent data loss** — every row loaded or recorded with reason | Each record is either accepted or excluded with one reason (R6); excluded rows persisted with raw content (`excluded_rows`); unparseable optional values are excluded, never nulled; empty lines counted | `test_partition_accounts_for_every_row`, `test_each_reason_persisted_with_raw_record`, `test_read_source_counts_blank_line_as_record` |
| I. Explicit schema before clean layers | 48-column source contract + strict parsers (`source_contract.py`); full pandera contracts are Phase 2 as the spec states | `test_check_header_*`, `test_source_contract_matches_db_columns` |
| I. Row counts reconcile and are logged | `rows_read = rows_loaded + rows_excluded` asserted in Python, enforced by a CHECK on `ingest_runs`, logged and printed per run | `test_ingest_report_counts`, `test_ingest_runs_check_rejects_unbalanced_counts` |
| **II. Idempotent pipelines** | Natural-key upserts for every table; excluded rows keyed by `(source_sha256, source_row_number)` | `test_second_run_leaves_identical_state` |
| II. Raw data kept unchanged | Content-addressed read-only raw copy, atomic rename (R13) | `test_download_keeps_unmodified_copy` |
| II. Safe to retry after partial failure | Single transaction + advisory lock; failed runs leave no DB trace | `test_db_failure_mid_load_rolls_back`, `test_concurrent_run_rejected` |
| **III. Unit tests for cleaning logic** | `tests/unit/` covers normalization, parsing, validation, partitioning, entity building | see FR/SC mapping |
| III. Integration tests on real PostgreSQL | testcontainers `postgres:17.6-alpine`; no mocks or SQLite | `tests/integration/` |
| III. API tests | N/A — no API in this feature | — |
| III. Done only when tests pass in CI | `.github/workflows/ci.yml` runs `scripts/check.sh`, image build, Compose smoke | T005, T043 |
| **IV. One-command containerized stack** | `docker compose up -d` brings up db + migrations; ingest is a profile job | `scripts/smoke_compose.sh` in CI |
| IV. Env config, no committed secrets | `.env` git-ignored, `.env.example` with placeholders; `DATABASE_URL` env | `test_config_*` |
| IV. Pinned versions | `uv.lock`, `postgres:17.6-alpine`, `python:3.12.11-slim-bookworm`, `uv==0.12.5` | Dockerfile, compose |
| **V. Simple, incremental** | No ORM, no HTTP/CLI/config libraries, no orchestration, no dbt; every dependency justified in research.md | research.md |
| Tech: Python `src/` + uv + ruff | Yes | pyproject.toml |
| Tech: PostgreSQL, versioned migrations | Alembic `0001_initial_schema`; app never issues DDL except `CREATE TEMP TABLE` | `test_upgrade_creates_schema` |
| Tech: PKs, FKs, indexes | All tables have PKs; FKs on matches/excluded_rows; indexes on FK columns and natural keys | data-model.md |
| Tech: transforms in SQL (dbt) | N/A — out of scope (spec) | — |
| Tech: attribution in README | T052 | README |
| Tech: macOS tooling | uv, Docker, psql — all macOS | — |
| Quality: typed code, static checker | mypy `--strict` in gate | `scripts/check.sh` |
| Quality: pure logic separate from I/O | `clean.py`, `validate.py` pure (DataFrame in → out); I/O only in `extract.py`, `load.py`, `pipeline.py`, `cli.py`, `verify.py` | unit tests run with sockets disabled and no DB |
| Quality: one module per stage, no cycles | `extract`, `clean`, `validate`, `load` modules; leaf modules `source_contract`, `errors`, `config` | import graph below |
| Quality: structured logging, no print | `logging` to stderr; ruff `T20` bans `print` | ruff |
| Quality: explicit error handling | ruff `BLE001`, `E722`, `S110`; typed error classes map to exit codes | `test_cli_exit_codes` |
| Quality: docstrings | ruff `D1xx` (google convention) on `src/` | ruff |
| Workflow: CI order lint → types → unit → integration → image build | `check.sh` then `docker build` in CI | ci.yml |
| Workflow: measured performance claims | SC-001/SC-002 timed manually, recorded in README (R25) | quickstart §1–2 |

**Result (pre-design)**: PASS. **Result (post-design re-check)**: PASS — the design added no
dependency or layer beyond those listed; `verify` (R24) is justified by SC-004 and the advisory lock
by Principle II (retry safety). No violations → Complexity Tracking is empty.

## Deterministic Quality Gate

`scripts/check.sh` (bash, `set -euo pipefail`, run from repo root, exits non-zero on the first
failure, prints a header per step). Steps, in this exact order:

1. `uv run --frozen ruff format --check .`
2. `uv run --frozen ruff check .`
3. `uv run --frozen mypy src tests` (Alembic revision files are excluded: their numeric file names are not importable module names; they contain only SQL strings)
4. `uv run --frozen pytest tests/unit --randomly-seed=20261011 --disable-socket`
5. `uv run --frozen pytest tests/integration --randomly-seed=20261011`

Determinism rules (enforced by construction and by the gate):
- No external network: fixtures under `tests/fixtures/`; downloads tested via `file://` and a
  `127.0.0.1` `http.server`; unit tests run with `--disable-socket` (pytest-socket).
- No time dependence: timestamps are never asserted; snapshots exclude `started_at`/`finished_at`.
- No randomness without a seed: test order seed fixed; `verify` uses `random.Random(seed)`.
- No order dependence: every integration test gets its own database cloned from a migrated template.
- Pinned toolchain: `uv.lock`, `--frozen`, pinned Postgres image.

CI (`.github/workflows/ci.yml`) runs `scripts/check.sh`, then `docker build`, then
`scripts/smoke_compose.sh` (not part of `check.sh`, because it builds images).

## FR / SC → Test Mapping

Unit tests live in `tests/unit/`, integration tests in `tests/integration/`. Names are exact; tasks
reference them.

| Req | What is verified | Test(s) (file :: name) |
|---|---|---|
| FR-001 | raw file downloaded, unmodified, content-addressed, kept per run; `file://` and HTTP | `integration/test_extract_download.py::test_download_keeps_unmodified_copy`, `::test_download_http_loopback_streams_and_hashes`, `::test_existing_raw_copy_reused`; `integration/test_ingest_pipeline.py::test_run_records_source_and_raw_path` |
| FR-002 | missing/renamed/duplicate columns stop the run, error names columns, DB untouched | `unit/test_validate_header.py::test_check_header_names_all_missing_columns`, `::test_check_header_rejects_duplicate_names`, `unit/test_extract_read.py::test_read_source_malformed_raises`, `::test_check_header_ignores_order_and_allows_extra`; `integration/test_ingest_pipeline.py::test_missing_columns_stops_before_db` |
| FR-003 | ISO dates parsed; invalid dates excluded `invalid_date` | `unit/test_clean_parsers.py::test_parse_date_valid`, `::test_parse_date_invalid`; `unit/test_validate_rows.py::test_invalid_date_excluded` |
| FR-004 | trim, collapse, casefold, NFC; one team per key; deterministic display name | `unit/test_clean_team_names.py::test_team_key_normalizes_formatting`, `::test_team_key_is_idempotent`, `::test_canonical_name_most_frequent_then_lexicographic`; `integration/test_ingest_pipeline.py::test_team_spelling_variants_single_team` |
| FR-005 | optional missing → NULL and loaded; required missing → excluded naming the field; missing result | `unit/test_validate_rows.py::test_missing_required_field_excluded` (parametrized ×4 + blank), `::test_missing_result_excluded`, `::test_optional_stats_missing_loaded_as_null`; `integration/test_ingest_pipeline.py::test_optional_nulls_stored_as_null` |
| FR-006 | duplicates on normalized key: first kept, rest `duplicate` with pointer | `unit/test_validate_rows.py::test_duplicate_keeps_first_excludes_rest`, `::test_duplicate_detected_after_normalization`; `integration/test_constraints.py::test_constraint_rejects_duplicate_natural_key` |
| FR-007 | leagues/teams/matches with FKs; each match → 1 league, 2 teams | `unit/test_clean_build.py::test_build_entities`, `::test_team_in_multiple_leagues_single_team`; `integration/test_migrations.py::test_upgrade_creates_schema`; `integration/test_ingest_pipeline.py::test_ingest_loads_valid_fixture` |
| FR-008 | home ≠ away and FK integrity enforced by DB; pipeline excludes `same_team` first | `integration/test_constraints.py::test_constraint_rejects_home_equals_away`, `::test_constraint_rejects_unknown_league`, `::test_constraint_rejects_unknown_team`; `unit/test_validate_rows.py::test_same_team_after_normalization_excluded` |
| FR-009 | schema only via Alembic; upgrade from empty; re-apply no-op; downgrade round-trip | `integration/test_migrations.py::test_upgrade_creates_schema`, `::test_upgrade_is_noop_at_head`, `::test_downgrade_then_upgrade_roundtrip`, `::test_source_contract_matches_db_columns` |
| FR-010 | report read/loaded/excluded + per reason; read = loaded + excluded | `unit/test_accounting.py::test_partition_accounts_for_every_row`, `::test_report_counts_per_reason`; `integration/test_ingest_pipeline.py::test_ingest_report_counts`; `integration/test_constraints.py::test_ingest_runs_check_rejects_unbalanced_counts`; `integration/test_cli.py::test_cli_ingest_report_matches_schema` |
| FR-011 | second run → identical tables (incl. ids), no duplicate excluded rows | `integration/test_idempotency.py::test_second_run_leaves_identical_state`, `::test_second_run_reports_all_unchanged` |
| FR-012 | DB failure mid-load, download failure, header failure, concurrent run → DB unchanged | `integration/test_atomicity.py::test_db_failure_mid_load_rolls_back` (empty and preloaded DB), `::test_concurrent_run_rejected`; `integration/test_extract_download.py::test_interrupted_download_leaves_no_file_and_db_unchanged`; `integration/test_ingest_pipeline.py::test_missing_columns_stops_before_db` |
| FR-013 | each exclusion persisted with raw content, reason, run | `integration/test_excluded_rows.py::test_each_reason_persisted_with_raw_record`, `::test_excluded_rows_link_to_run` |
| SC-001 | ≤ 2 commands, < 10 min from fresh checkout | **Manual, measured** (quickstart §1); automated container path covered by `scripts/smoke_compose.sh` in CI |
| SC-002 | full load < 5 min | **Manual, measured** (quickstart §2; research.md R25 explains why not asserted) |
| SC-003 | 100% of rows accounted for | `unit/test_accounting.py::test_partition_accounts_for_every_row` (all fixtures); `integration/test_ingest_pipeline.py::test_ingest_report_counts`, `::test_db_reconciles_with_run_counts` |
| SC-004 | 0 differences in date/teams/score vs source | `integration/test_verify.py::test_every_loaded_match_equals_source` (stdlib-csv oracle, all rows), `::test_verify_command_reports_no_differences`, `::test_verify_detects_tampered_row`; real data: quickstart §9 |
| SC-005 | identical counts after two runs | `integration/test_idempotency.py::test_second_run_leaves_identical_state` |
| SC-006 | each team once despite whitespace/casing | `unit/test_clean_team_names.py::test_team_key_normalizes_formatting`; `integration/test_ingest_pipeline.py::test_team_spelling_variants_single_team` |
| US1-2 | query by league + season (date range) | `integration/test_ingest_pipeline.py::test_query_by_league_and_date_range` |
| US2-1 | DB available without manual config | `scripts/smoke_compose.sh` (CI) |
| Edge: unreachable / interrupted source | exit 3, DB unchanged, no partial file | `integration/test_extract_download.py::test_unreachable_source_raises`, `::test_interrupted_download_leaves_no_file_and_db_unchanged`; `integration/test_cli.py::test_cli_exit_codes` |
| Edge: header-only file | succeeds with 0/0/0 | `integration/test_ingest_pipeline.py::test_header_only_file_loads_nothing` |

## Test Fixtures (`tests/fixtures/`)

All fixtures use the real 48-column header (research.md R1). Each edge-case row's expected outcome
is recorded in a sidecar JSON so tests are table-driven.

| File | Content | Used for |
|---|---|---|
| `matches_valid.csv` | 12 valid rows, leagues `E0` and `E1`; one team in both leagues; some rows with empty optional stats and empty `MatchTime` | US1, FR-005 optional, FR-007, SC-004 |
| `matches_edge_cases.csv` + `matches_edge_cases.expected.json` | one row per exclusion reason and variant: blank line; missing `Division`/`MatchDate`/`HomeTeam`/`AwayTeam`; whitespace-only `HomeTeam`; invalid dates `2021-02-30`, `03/12/2020`; missing `FTHome`; `FTHome`=`2.5`; `HomeShots`=`-1`; `MatchTime`=`25:00:00`; `FTResult`=`X`; `OddHome`=`nan`; `Division`=`E 0`; same team `Ajax` v `ajax `; exact duplicate; duplicate differing only in case/whitespace; team variants `Ajax `/`Ajax`, `Colon Santa FE`/`Colon Santa Fe`, `Man  City`/`Man City`; a row failing two rules (precedence) | FR-003…FR-006, FR-008, FR-010, FR-013, SC-003, SC-006 |
| `matches_missing_columns.csv` | header without `FTHome`, `AwayTeam` renamed `Away_Team`; 2 rows | FR-002 |
| `matches_duplicate_header.csv` | header with `HomeTeam` twice | FR-002 |
| `matches_header_only.csv` | header, no rows | edge |
| `matches_ragged.csv` | one row with 47 fields | FR-002/R4 malformed source (exit 4) |
| `matches_invalid_utf8.csv` | byte `0xFF` inside a team name | R4 malformed source (exit 4) |
| `matches_real_sample.csv` | header + real rows from the pinned file: data records 1–10 and file lines 64347 (`Ajax `), 73508 (`Colon Santa FE`), 192326 (`Colon Santa Fe`), 112582 (missing result), 174503 (`King\u0092s Lynn`), 230559 (empty line) | realism check of parsers on real formatting |

## Project Structure

### Documentation (this feature)

```text
specs/001-match-data-ingestion/
├── plan.md              # This file
├── research.md          # Phase 0: decisions R1–R26
├── data-model.md        # Phase 1: tables, column contract, parity table
├── quickstart.md        # Phase 1: manual acceptance run
├── contracts/
│   ├── cli.md                    # CLI, env, exit codes, Compose commands
│   └── run-report.schema.json    # stdout report JSON Schema
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
pyproject.toml            # uv project, ruff/mypy/pytest config, script entry point
uv.lock
.python-version
.gitignore                # .env, data/raw/, .venv, caches
.dockerignore
.env.example              # POSTGRES_USER/PASSWORD/DB/PORT placeholders, LOG_LEVEL
Dockerfile                # python:3.12.11-slim-bookworm + uv==0.12.5, non-root
docker-compose.yml        # db, migrate, ingest (profile)
alembic.ini
README.md
migrations/
├── env.py                # reads DATABASE_URL, psycopg driver
├── script.py.mako
└── versions/
    └── 0001_initial_schema.py
scripts/
├── check.sh              # the quality gate
└── smoke_compose.sh      # CI: compose migrate ×2 + ingest a fixture in the container
src/football_stats/
├── __init__.py
├── errors.py             # error classes ↔ exit codes (leaf)
├── config.py             # Settings from env (leaf)
├── source_contract.py    # 48 expected columns, ColumnSpec, ExclusionReason (leaf)
├── extract.py            # download/copy raw file, read header, read body  [I/O]
├── clean.py              # parsers, team normalization, entity building    [pure]
├── validate.py           # header check, row rules, precedence, dedupe     [pure]
├── load.py               # transaction, COPY, upserts, advisory lock       [I/O]
├── pipeline.py           # run(): extract → clean → validate → load, report [I/O]
├── verify.py             # SC-004 comparison                               [I/O]
└── cli.py                # argparse, logging setup, exit codes, stdout JSON
tests/
├── conftest.py           # fixture paths, CSV builder helper
├── fixtures/             # see table above
├── unit/
│   ├── test_config.py
│   ├── test_extract_read.py
│   ├── test_validate_header.py
│   ├── test_clean_parsers.py
│   ├── test_clean_team_names.py
│   ├── test_validate_rows.py
│   ├── test_accounting.py
│   └── test_clean_build.py
└── integration/
    ├── conftest.py       # PostgresContainer, migrated template DB, per-test DB, loopback HTTP
    ├── test_migrations.py
    ├── test_constraints.py
    ├── test_extract_download.py
    ├── test_ingest_pipeline.py
    ├── test_excluded_rows.py
    ├── test_idempotency.py
    ├── test_atomicity.py
    ├── test_verify.py
    └── test_cli.py
.github/workflows/ci.yml
```

Import graph (no cycles): `cli → pipeline, verify, config, errors`; `pipeline → extract, clean,
validate, load, source_contract, errors`; `verify → extract, clean, source_contract`;
`clean`, `validate` → `source_contract` only (+ polars); `load → source_contract, errors` (+ psycopg).

**Structure Decision**: single Python project with `src/` layout (constitution), package
`football_stats` as in PLAN.md's suggested layout. `api/`, `dbt/`, `k8s/`, `flows.py` are not
created in this feature.

## Complexity Tracking

No constitution violations. Items a reviewer might question, with justification:

| Item | Why needed | Simpler alternative rejected because |
|---|---|---|
| `verify` subcommand | SC-004 must be checkable on the real dataset, which tests cannot download | manual psql comparison of 48 columns is error-prone and unrepeatable |
| Advisory lock | Two concurrent runs can deadlock on the unique index; Principle II retry safety | relying on "don't do that" leaves a real failure mode untested |
| `invalid_value` / `same_team` reasons beyond the spec's examples | constraint parity: without them a single bad value aborts the whole run (FR-012) or is silently nulled (Principle I) | nulling bad optional values = silent data loss |
| pytest-randomly, pytest-socket, jsonschema (dev only) | enforce no order reliance, no network in unit tests, and the report contract | conventions without enforcement drift |
