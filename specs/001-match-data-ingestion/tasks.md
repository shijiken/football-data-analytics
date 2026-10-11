---

description: "Task list for 001-match-data-ingestion"
---

# Tasks: Match Data Ingestion

**Input**: Design documents from `/specs/001-match-data-ingestion/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli.md,
contracts/run-report.schema.json, quickstart.md

**Tests**: Required. The constitution (Principle III) and the planner rules require tests written
**before** the code they cover. Every test task names the exact test functions from the
FR/SC → Test Mapping in plan.md. A test task is done when its tests exist, are collected, and fail
for the expected reason (missing implementation); the paired implementation task is done when they
pass and `scripts/check.sh` exits 0.

**Organization**: Grouped by user story (spec.md priorities P1 → P3) after shared Setup and
Foundational phases.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1, US2, US3 (user story phases only)

## Path Conventions

Single project, `src/` layout: `src/football_stats/`, `tests/unit/`, `tests/integration/`,
`tests/fixtures/`, `migrations/`, `scripts/` at the repository root.

## Global rules for every task

- Python code: full type hints (mypy `--strict`), google-style docstrings on public functions,
  `logging` only (no `print`), no bare/blind `except`, no swallowed exceptions.
- `clean.py` and `validate.py` MUST NOT import `psycopg`, `urllib`, `socket`, `pathlib` I/O helpers
  or open files: data in → data out.
- Tests: no external network, no timestamp assertions, no reliance on test order or on data left by
  another test.
- Tests never write into the repository: every integration test that builds `Settings` uses the
  shared `settings` fixture (T012), whose `raw_data_dir` is `tmp_path`; every CLI subprocess sets
  `RAW_DATA_DIR` to `tmp_path` explicitly. Each test names the fixture CSV it ingests.
- Parser regexes use `[0-9]`, never `\d` (data-model.md §2.1).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project skeleton and the quality gate exist before any feature code.

- [x] T001 Create the project skeleton and the quality gate: `pyproject.toml` (name `football-stats`, `requires-python = ">=3.12,<3.13"`, build backend `uv_build`, dependencies `polars>=1.30,<2`, `psycopg[binary]>=3.2,<3.3`, `alembic>=1.16,<1.17`, `sqlalchemy>=2.0,<2.1`; dependency group `dev`: `ruff`, `mypy`, `pytest>=8,<9`, `testcontainers>=4,<5`, `pytest-randomly`, `pytest-socket`, `jsonschema`, `types-jsonschema`; `[project.scripts] football-stats = "football_stats.cli:main"`; ruff `line-length = 100`, `select = ["E","F","W","I","B","UP","SIM","T20","BLE","S110","D","PL","RUF"]`, pydocstyle convention `google`, per-file-ignores relaxing `D` and `PLR2004` for `tests/**` and `migrations/**`; mypy `strict = true`, `files = ["src","tests"]`, override `ignore_missing_imports = true` for `testcontainers.*`; pytest `addopts = "--strict-markers -ra"`, `xfail_strict = true`), `.python-version` (`3.12`), `src/football_stats/__init__.py` (module docstring, `__version__ = "0.1.0"`), `.gitignore` (`.env`, `data/raw/`, `.venv/`, `__pycache__/`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`), and executable `scripts/check.sh` (`#!/usr/bin/env bash`, `set -euo pipefail`, `cd` to repo root, then exactly these steps in order, each preceded by an `echo "==> <step>"` header: `uv run --frozen ruff format --check .`; `uv run --frozen ruff check .`; `uv run --frozen mypy src tests`; `uv run --frozen pytest tests/unit --randomly-seed=20261011 --disable-socket`; `uv run --frozen pytest tests/integration --randomly-seed=20261011`); run `uv lock` to create `uv.lock`
- [x] T002 [P] Create `.env.example` (`POSTGRES_USER=football`, `POSTGRES_PASSWORD=change-me-local-only`, `POSTGRES_DB=football`, `POSTGRES_PORT=5432`, `LOG_LEVEL=INFO`, commented `SOURCE_URL` showing the pinned default from research.md R1) and `.dockerignore` (`.git`, `.venv`, `data/`, `.env`, caches, `specs/`)
- [x] T003 [P] Create `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py` and `tests/unit/test_package.py::test_package_imports_and_has_version` (imports `football_stats`, asserts `__version__ == "0.1.0"`)
- [x] T004 Create `tests/integration/conftest.py` with: session fixture `postgres_container` (`testcontainers.postgres.PostgresContainer("postgres:17.6-alpine", driver=None)`), session fixture `admin_url` (libpq URL to the `postgres` database), function fixture `empty_db_url` (creates database `test_<counter>` with `CREATE DATABASE`, yields its URL, drops it with `WITH (FORCE)`), function fixture `loopback_http_server` (stdlib `http.server.ThreadingHTTPServer` bound to `127.0.0.1` port 0 in a daemon thread, serving a dict of path → (status, headers, body, truncate_after) so tests can simulate short reads; shut down in teardown); and `tests/integration/test_infrastructure.py::test_empty_db_accepts_connections`, `::test_loopback_server_serves_bytes`
- [x] T005 [P] Create `.github/workflows/ci.yml`: trigger on push and pull_request; job `gate` on `ubuntu-24.04`: `actions/checkout@v4`, `astral-sh/setup-uv@v6` with `version: "0.12.5"`, `uv sync --frozen`, `scripts/check.sh` (image build and Compose smoke are added in T043)
- [x] T006 Run `scripts/check.sh` and fix until it exits 0 (gate green on the empty skeleton)

**Checkpoint**: `scripts/check.sh` exits 0; CI runs it.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Error types, configuration, the source column contract, test fixtures and the database
schema — every user story needs all of them.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Tests first

- [x] T007 [P] Write `tests/unit/test_config.py`: `test_config_reads_env` (all four variables), `test_config_defaults` (`SOURCE_URL` = pinned URL from research.md R1, `RAW_DATA_DIR` = `data/raw`, `LOG_LEVEL` = `INFO`), `test_config_missing_database_url_raises` (`ConfigurationError` naming `DATABASE_URL`), `test_config_invalid_log_level_raises`; build settings from an explicit `Mapping[str, str]` argument, never from the real process environment
- [x] T008 [P] Write `tests/unit/test_source_contract.py`: `test_expected_columns_match_real_header` (the 48 names in the exact order of research.md R1, as a literal in the test), `test_every_expected_column_has_one_spec`, `test_db_columns_unique_and_snake_case`, `test_kinds` (key = `Division`,`MatchDate`,`HomeTeam`,`AwayTeam`; required = `FTHome`,`FTAway`,`FTResult`; the other 41 optional), `test_exclusion_reasons_in_precedence_order` (`missing_field`, `invalid_date`, `missing_result`, `invalid_value`, `same_team`, `duplicate`)
- [x] T009 Create test fixtures in `tests/fixtures/` (all with the exact 48-column header from research.md R1 unless stated): `matches_valid.csv` (12 valid rows, leagues `E0` and `E1`, one team in both leagues, ≥2 rows with all optional stats empty, ≥2 rows with empty `MatchTime`); `matches_edge_cases.csv` with one row per case listed in plan.md "Test Fixtures" (blank line; missing `Division`/`MatchDate`/`HomeTeam`/`AwayTeam`; whitespace-only `HomeTeam`; `MatchDate` `2021-02-30` and `03/12/2020`; empty `FTHome`; `FTHome`=`2.5`; `HomeShots`=`-1`; `HomeShots`=`40000`; `MatchTime`=`25:00:00`; `FTResult`=`X`; `OddHome`=`nan`; `Division`=`E 0`; `Ajax` v `ajax `; an exact duplicate; a duplicate differing only in team case/whitespace; valid rows with `Ajax `/`Ajax`, `Colon Santa FE`/`Colon Santa Fe` ×2 and `Colon Santa FE` ×1, `Man  City`/`Man City`; one row with both missing `HomeTeam` and invalid date) plus `matches_edge_cases.expected.json` mapping every `source_row_number` to `"loaded"` or `{"reason": <code>, "detail": <exact detail string>}`; `matches_missing_columns.csv` (no `FTHome`, `AwayTeam` renamed `Away_Team`, 2 rows); `matches_duplicate_header.csv` (`HomeTeam` twice); `matches_header_only.csv`; `matches_ragged_short.csv` (one row with 47 fields — polars alone would silently pad it); `matches_ragged_long.csv` (one row with 49 fields); `matches_invalid_utf8.csv` (a byte `0xFF` inside a team name); `matches_real_sample.csv` built once with `curl -sSL <pinned URL from research.md R1> | awk 'NR==1 || (NR>=2 && NR<=11) || NR==64347 || NR==73508 || NR==112582 || NR==174503 || NR==192326 || NR==230559' > tests/fixtures/matches_real_sample.csv` (record the command and source line numbers in a `tests/fixtures/SOURCES.txt`, including the MIT licence and attribution of the dataset); plus `tests/conftest.py` with a `fixtures_dir` fixture and helper `fixture_path(name: str) -> Path`
- [x] T010 [P] Write `tests/integration/test_migrations.py` (uses `empty_db_url`; runs Alembic programmatically via `alembic.config.Config` + `alembic.command`): `test_upgrade_creates_schema` (tables `leagues`, `teams`, `matches`, `ingest_runs`, `excluded_rows`; PKs; FKs `matches.league_code→leagues`, `matches.home_team_id/away_team_id→teams`, `excluded_rows.run_id→ingest_runs`; unique `matches_natural_key`, `excluded_rows_source_key`, `teams.team_key`; indexes `matches_home_team_id_idx`, `matches_away_team_id_idx`, `excluded_rows_run_id_idx`; queried from `pg_catalog`), `test_upgrade_is_noop_at_head` (second `upgrade head` succeeds; `helpers.catalog_snapshot(conn)` and `alembic_version` unchanged), `test_downgrade_then_upgrade_roundtrip` (downgrade `base` leaves only `alembic_version`, then upgrade recreates an identical `helpers.catalog_snapshot(conn)`), `test_source_contract_matches_db_columns` (every `ColumnSpec.db_column` exists in `matches` with the type from data-model.md §1.3, and `matches` has no other columns besides `match_id`, `home_team_id`, `away_team_id`, `source_sha256`, `source_row_number`)
- [x] T011 [P] Write `tests/integration/test_constraints.py` (direct SQL against the migrated template clone; each expects `psycopg.errors.<Specific>` and nothing else): `test_constraint_rejects_bad_league_code`, `test_constraint_rejects_blank_team`, `test_constraint_rejects_home_equals_away`, `test_constraint_rejects_duplicate_natural_key`, `test_constraint_rejects_null_required` (parametrized over `match_date`, `home_team_id`, `ft_home_goals`, `ft_result`), `test_constraint_rejects_negative_goals`, `test_constraint_rejects_bad_result`, `test_constraint_rejects_unknown_league`, `test_constraint_rejects_unknown_team`, `test_ingest_runs_check_rejects_unbalanced_counts`, `test_excluded_rows_reason_check`
- [x] T012 Extend `tests/integration/conftest.py`: session fixture `template_db` (creates database `football_template`, runs `alembic upgrade head` against it, marks it `IS_TEMPLATE`), function fixtures `db_url` (`CREATE DATABASE test_<counter> TEMPLATE football_template`, drop after) and `db_conn` (psycopg connection, autocommit off, closed after); and `tests/integration/helpers.py` with `snapshot(conn) -> dict[str, list[tuple[object, ...]]]` returning all rows of `leagues`, `teams`, `matches`, `excluded_rows` ordered by primary key, and of `ingest_runs` ordered by `run_id` excluding `started_at`/`finished_at`, `table_counts(conn) -> dict[str, int]`, and `catalog_snapshot(conn) -> dict[str, list[tuple[object, ...]]]` returning, for schema `public`, ordered rows of `information_schema.columns` (`table_name, column_name, data_type, is_nullable, column_default`), `pg_constraint` joined to `pg_class` (`relname, conname, contype, pg_get_constraintdef(oid)`) and `pg_indexes` (`tablename, indexname, indexdef`) — pure SQL, no `pg_dump`; plus function fixture `settings(db_url, tmp_path) -> Settings` (`database_url=db_url`, `raw_data_dir=tmp_path / "raw"`, `source_url` = a `file://` URL that tests override, `log_level="DEBUG"`)

### Implementation

- [x] T013 [P] Implement `src/football_stats/errors.py`: base `FootballStatsError(Exception)` with class attributes `exit_code: int` and `error: str`; subclasses `ConfigurationError` (2, `configuration_error`), `SourceUnavailableError` (3, `source_unavailable`), `MissingColumnsError` (4, `missing_columns`; attribute `missing: tuple[str, ...]`; message `Source file is missing expected columns: <comma-separated, contract order>`), `MalformedSourceError` (4, `malformed_source`), `ConcurrentRunError` (5, `concurrent_run`), `VerificationFailedError` (6, `verification_failed`), `LoadError` (7, `database_error`), `AccountingError` (8, `accounting_error`) — exactly the table in contracts/cli.md
- [x] T014 Implement `src/football_stats/config.py`: frozen dataclass `Settings(database_url, source_url, raw_data_dir: Path, log_level)` and `load_settings(env: Mapping[str, str]) -> Settings` with defaults from contracts/cli.md (makes T007 pass)
- [x] T015 Implement `src/football_stats/source_contract.py`: `EXPECTED_COLUMNS: tuple[str, ...]` (48 names, file order), `ColumnKind` (`KEY`, `REQUIRED`, `OPTIONAL`), `Parser` (`TEXT`, `LEAGUE`, `TEAM`, `DATE`, `TIME`, `COUNT`, `DECIMAL`, `RESULT`), frozen dataclass `ColumnSpec(source_name, db_column, kind, parser)`, `COLUMN_SPECS` exactly per data-model.md §1.3 table (e.g. `FTHome → ft_home_goals, REQUIRED, COUNT`; `HomeTarget → home_shots_on_target, OPTIONAL, COUNT`; `C_LTH → cluster_lth, OPTIONAL, DECIMAL`), `ExclusionReason(StrEnum)` in precedence order, `PINNED_SOURCE_URL` constant (makes T008 pass)
- [x] T016 Create Alembic scaffolding: `alembic.ini` (`script_location = migrations`, no URL in file), `migrations/env.py` (reads `DATABASE_URL` from env or `config.attributes["database_url"]`, rewrites `postgresql://` to `postgresql+psycopg://`, online mode only, `transaction_per_migration = True`), `migrations/script.py.mako`
- [x] T017 Write `migrations/versions/0001_initial_schema.py` (revision `"0001_initial_schema"`, `down_revision = None`) with `op.execute()` SQL creating exactly the tables, columns, types, nullability, constraint names and indexes of data-model.md §1: `leagues.league_code text PRIMARY KEY CHECK (league_code ~ '^[A-Z0-9]{1,10}$')`; `teams(team_id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY, team_key text NOT NULL UNIQUE CHECK (team_key <> ''), name text NOT NULL CHECK (btrim(name) <> ''))`; `matches` with `CHECK (home_team_id <> away_team_id)`, `CONSTRAINT matches_natural_key UNIQUE (league_code, match_date, home_team_id, away_team_id)`, all count columns `smallint CHECK (col >= 0)`, `ft_result char(1) NOT NULL CHECK (ft_result IN ('H','D','A'))`, `ht_result char(1) CHECK (ht_result IN ('H','D','A'))`, `source_sha256 char(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$')`, `source_row_number integer NOT NULL CHECK (source_row_number >= 1)`; `ingest_runs` with `CHECK (rows_read = rows_loaded + rows_excluded)` and `CHECK (rows_loaded = matches_inserted + matches_updated + matches_unchanged)`; `excluded_rows` with `reason text NOT NULL CHECK (reason IN ('missing_field','invalid_date','missing_result','invalid_value','same_team','duplicate'))`, `CONSTRAINT excluded_rows_source_key UNIQUE (source_sha256, source_row_number)`; indexes `matches_home_team_id_idx`, `matches_away_team_id_idx`, `excluded_rows_run_id_idx`; `downgrade()` drops all five tables in reverse dependency order (makes T010, T011 pass)
- [x] T018 Run `scripts/check.sh` and fix until it exits 0

**Checkpoint**: Schema migrates from empty, re-applies as a no-op, and enforces every constraint;
fixtures exist for every exclusion reason.

---

## Phase 3: User Story 1 - Load match data into a queryable database (Priority: P1) 🎯 MVP

**Goal**: `football-stats ingest` downloads (or copies) the source, keeps the raw copy, checks the
header, cleans and validates, and upserts leagues/teams/matches plus a run record in one
transaction, printing the JSON report.

**Independent Test**: On a fresh migrated database, `football-stats ingest --source-file
tests/fixtures/matches_valid.csv` exits 0, the report shows 12/12/0, and every match links to its
league and two teams (`test_ingest_loads_valid_fixture`).

### Tests for User Story 1 (write first, must fail)

- [x] T019 [P] [US1] Write `tests/unit/test_extract_read.py`: `test_read_header_returns_raw_names` (duplicates not renamed), `test_read_source_all_strings_with_row_numbers` (every column `pl.String`, `source_row_number` 1..n), `test_read_source_counts_blank_line_as_record` (pins the polars behaviour of research.md R4), `test_read_source_preserves_whitespace` (`"Ajax "` unchanged), `test_check_structure_accepts_valid_and_blank_lines` (`matches_valid.csv`, `matches_real_sample.csv`), `test_read_source_malformed_raises` (parametrized `matches_ragged_short.csv`, `matches_ragged_long.csv`, `matches_invalid_utf8.csv` → `MalformedSourceError`; for the ragged files the message names the record number and its field count)
- [x] T020 [P] [US1] Write `tests/unit/test_validate_header.py`: `test_check_header_accepts_real_header`, `test_check_header_names_all_missing_columns` (`matches_missing_columns.csv` header → `MissingColumnsError.missing == ("AwayTeam", "FTHome")` and message lists both), `test_check_header_rejects_duplicate_names` (`MalformedSourceError` naming `HomeTeam`), `test_check_header_ignores_order_and_allows_extra` (returns the extra names for a warning)
- [x] T021 [P] [US1] Write `tests/unit/test_clean_parsers.py`: `test_parse_date_valid`, `test_parse_date_invalid` (parametrized `2021-02-30`, `03/12/2020`, `2021-13-01`, `20210101`, `2021-1-5`, `٢٠٢١-01-05`), `test_parse_time` (valid `19:45:00`; invalid `25:00:00`, `19:45`, `7pm`), `test_parse_count` (`3.0`→3, `0`→0; invalid `2.5`, `-1`, `40000`, `nan`, `1e2`, `٣` (Arabic-Indic digit three)), `test_parse_decimal` (`1.65`, `-0.25`; invalid `nan`, `inf`, `1e5`, `1,5`), `test_parse_result` (`H`/`D`/`A`; invalid `X`, `h`), `test_normalize_league` (` e0 `→`E0`; `E 0` invalid); each parser test asserts value-or-invalid flag and that empty/whitespace → null without invalid flag
- [x] T022 [P] [US1] Write `tests/unit/test_clean_team_names.py`: `test_team_key_normalizes_formatting` (parametrized `"Ajax "`, `" ajax"`, `"AJAX"` → `ajax`; `"Man  City"`→`man city`; tab/NBSP whitespace; NFD `Münster` == NFC `Münster`; `Preußen` casefold → `preussen`), `test_team_key_is_idempotent`, `test_canonical_name_most_frequent_then_lexicographic` (`Colon Santa Fe` ×2 vs `Colon Santa FE` ×1 → `Colon Santa Fe`; a 1–1 tie → code-point smallest), `test_canonical_name_independent_of_row_order` (shuffle input with `random.Random(0)` → same result)
- [x] T023 [P] [US1] Write `tests/unit/test_validate_rows.py` (pure; build frames via clean + validate functions from `tests/fixtures/matches_edge_cases.csv` or inline rows): `test_edge_case_fixture_outcomes_match_expected` (every row's outcome equals `matches_edge_cases.expected.json`), `test_missing_required_field_excluded` (parametrized `Division`, `MatchDate`, `HomeTeam`, `AwayTeam`, whitespace-only `HomeTeam`, blank line → `missing_field`, detail names the column(s)), `test_invalid_date_excluded`, `test_missing_result_excluded`, `test_optional_stats_missing_loaded_as_null`, `test_invalid_value_excluded_names_column` (parametrized ids `division`, `fraction`, `negative`, `overflow`, `time`, `result`, `nan_odd` → `invalid_value`, detail is the source column name), `test_same_team_after_normalization_excluded`, `test_duplicate_keeps_first_excludes_rest` (detail `duplicate of source row <n>` pointing to the lowest row number), `test_duplicate_detected_after_normalization`, `test_reason_precedence` (missing `HomeTeam` + invalid date → `missing_field`; invalid date + missing result → `invalid_date`), `test_invalid_row_never_wins_duplicate` (an earlier invalid copy does not cause a later valid copy to be a duplicate)
- [x] T024 [P] [US1] Write `tests/unit/test_accounting.py`: `test_partition_accounts_for_every_row` (parametrized over `matches_valid.csv`, `matches_edge_cases.csv`, `matches_real_sample.csv`, `matches_header_only.csv`: accepted ∪ excluded row numbers = 1..rows_read, disjoint), `test_partition_rejects_inconsistent_counts` (constructing a `Partition` whose `accepted.height + excluded.height != rows_read` raises `AccountingError`), `test_report_counts_per_reason` (all six reason keys present, sum = excluded; `matches_real_sample.csv` → `missing_result` 1, `missing_field` 1, loaded 14)
- [x] T025 [P] [US1] Write `tests/unit/test_clean_build.py`: `test_build_entities` (from `matches_valid.csv`: league codes, one team per key with canonical name, matches frame with every `ColumnSpec.db_column` correctly typed plus `home_team_key`, `away_team_key`, `source_row_number`), `test_team_in_multiple_leagues_single_team`
- [x] T026 [P] [US1] Write `tests/integration/test_extract_download.py`: `test_download_keeps_unmodified_copy` (`file://` URL of `matches_valid.csv` → `RAW_DATA_DIR/matches-<sha>.csv` byte-identical, mode `0o444`, returned sha/bytes correct), `test_download_http_loopback_streams_and_hashes` (via `loopback_http_server`), `test_existing_raw_copy_reused` (second fetch → same path, no `.partial-*` left), `test_unreachable_source_raises` (HTTP 404 and refused port → `SourceUnavailableError`), `test_interrupted_download_leaves_no_file_and_db_unchanged` (server advertises `Content-Length` larger than body and closes; `pipeline.run` raises `SourceUnavailableError`; no file in `RAW_DATA_DIR`; `snapshot(db_conn)` unchanged)
- [x] T027 [P] [US1] Write `tests/integration/test_ingest_pipeline.py` (calls `pipeline.run(settings, source_file=...)` against `db_url`): `test_ingest_loads_valid_fixture` (`matches_valid.csv`: 12 matches, 2 leagues, expected teams; every match has league + two distinct teams), `test_query_by_league_and_date_range` (`matches_valid.csv`: the quickstart §4 SQL shape on fixture data returns the expected rows with date, teams, score), `test_team_spelling_variants_single_team` (edge-case fixture → one team each for `ajax`, `colon santa fe`, `man city`, names `Ajax`, `Colon Santa Fe`, `Man City`), `test_optional_nulls_stored_as_null` (`matches_valid.csv`), `test_ingest_report_counts` (`matches_edge_cases.csv`: report equals the totals derived from `matches_edge_cases.expected.json`; read = loaded + excluded; uses only the report and `matches`/`ingest_runs`, not `excluded_rows`), `test_run_records_source_and_raw_path` (`matches_valid.csv`: `ingest_runs` row has URL, sha, bytes, raw path that exists under `tmp_path`), `test_missing_columns_stops_before_db` (`matches_missing_columns.csv` on a DB preloaded with `matches_valid.csv`: `MissingColumnsError`, snapshot unchanged, no `ingest_runs` row), `test_header_only_file_loads_nothing` (`matches_header_only.csv`: 0/0/0, one `ingest_runs` row)
- [x] T028 [P] [US1] Write `tests/integration/test_atomicity.py`: `test_db_failure_mid_load_rolls_back` (parametrized `empty`, `preloaded` — preloaded first ingests `matches_valid.csv`; then calls `load.load_run` with a hand-built `CleanBatch` whose leagues/teams are valid but one match has home key = away key, bypassing validation, so the `matches` CHECK fails after leagues/teams were inserted; asserts `LoadError` and `snapshot` equal to before), `test_concurrent_run_rejected` (a second connection holds `pg_advisory_lock(load.LOCK_KEY)`; `pipeline.run` raises `ConcurrentRunError`; snapshot unchanged)
- [x] T029 [P] [US1] Write `tests/integration/test_cli.py` (runs `football-stats` via `subprocess.run([sys.executable, "-m", "football_stats.cli", ...])` with an explicit `env`): `test_cli_ingest_report_matches_schema` (`matches_valid.csv`; stdout parses as JSON and validates against `specs/001-match-data-ingestion/contracts/run-report.schema.json`; exit 0), `test_cli_exit_codes` (parametrized: missing `DATABASE_URL` → 2; refused-port `SOURCE_URL` → 3; `matches_missing_columns.csv` → 4 with `error` `missing_columns`; `matches_ragged_short.csv` → 4 `malformed_source`; advisory lock held → 5; `DATABASE_URL` pointing at a refused loopback port with `--source-file matches_valid.csv` → 7 `database_error`; each failure report validates against the schema), `test_cli_stdout_is_only_json` (stderr has log lines incl. the run summary; stdout is exactly one JSON document)

### Implementation for User Story 1

- [x] T030 [US1] Implement `src/football_stats/extract.py`: `read_header(path) -> list[str]` (stdlib `csv`, UTF-8 strict), `check_structure(path, expected_fields: int) -> None` (one stdlib-`csv` pass, strict UTF-8; any record whose field count is neither 0 nor `expected_fields` → `MalformedSourceError("record <n> has <k> fields, expected <expected_fields>")`; `UnicodeDecodeError` → `MalformedSourceError`), `read_source(path) -> pl.DataFrame` (`pl.read_csv(infer_schema=False, encoding="utf8")`, adds `source_row_number` UInt32 starting at 1; polars `ComputeError`/`NoDataError`/`UnicodeDecodeError` → `MalformedSourceError`; header-only file → empty frame with all columns), `fetch_to_raw(source: str | Path, raw_dir: Path) -> RawFile(path, sha256, bytes, source_url)` (stream 1 MiB chunks via `urllib.request.urlopen(timeout=60)` or local copy; hash while streaming; verify `Content-Length`; write `.partial-<pid>`, `os.replace` to `matches-<sha256>.csv`, `chmod 0o444`; reuse existing target; delete partial on any error; `URLError`/`HTTPError`/`TimeoutError`/`IncompleteRead`/`FileNotFoundError` → `SourceUnavailableError`) (makes T019, T026 pass)
- [x] T031 [US1] Implement `validate.check_header(header: Sequence[str]) -> list[str]` in `src/football_stats/validate.py` (raises `MissingColumnsError` with missing names in `EXPECTED_COLUMNS` order; raises `MalformedSourceError` naming any duplicated header name; returns the extra columns) (makes T020 pass)
- [x] T032 [US1] Implement parsers in `src/football_stats/clean.py`: `parse_frame(raw: pl.DataFrame) -> pl.DataFrame` adding, for every `ColumnSpec`, a typed column `<db_column>` and a boolean `<db_column>__invalid`, using exactly the regexes of data-model.md §2.1 (`date` `^[0-9]{4}-[0-9]{2}-[0-9]{2}$` + calendar check; `time` `^[0-9]{2}:[0-9]{2}:[0-9]{2}$` with ranges; `count` `^[0-9]+(\.0+)?$` and ≤ 32767; `decimal` `^-?[0-9]+(\.[0-9]+)?$`; `result` `H|D|A`; `league` upper + `^[A-Z0-9]{1,10}$`); values stripped first, blank → null and not invalid (makes T021 pass)
- [x] T033 [US1] Implement team normalization in `src/football_stats/clean.py`: `team_key(name: str) -> str` (`casefold(collapse_ws(strip(NFC(name))))`), `display_name(name: str) -> str` (same without casefold), `add_team_keys(frame) -> pl.DataFrame` (maps the distinct raw names through Python and joins back as `home_team_key`, `away_team_key`, `home_team_display`, `away_team_display`; blank → null), `canonical_team_names(accepted: pl.DataFrame) -> pl.DataFrame` (`team_key`, `name`: most frequent display among home+away occurrences, tie → smallest string) (makes T022 pass)
- [x] T034 [US1] Implement `validate.partition(parsed: pl.DataFrame) -> Partition` in `src/football_stats/validate.py`: frozen dataclass `Partition(accepted: pl.DataFrame, excluded: pl.DataFrame, rows_read: int)`; evaluates rules in the order `missing_field` → `invalid_date` → `missing_result` → `invalid_value` → `same_team` → `duplicate` (research.md R6), one reason + exact detail string per excluded row, duplicates evaluated only among rows passing rules 1–5 keeping the lowest `source_row_number`; `excluded` carries `source_row_number`, `reason`, `detail`, and `raw_record` (dict of every original column → original string or null); method `counts_by_reason() -> dict[str, int]` with all six keys; `Partition.__post_init__` raises `AccountingError` if `accepted.height + excluded.height != rows_read` (explicit check, never `assert`) (makes T023, T024 pass)
- [x] T035 [US1] Implement `clean.build_batch(accepted: pl.DataFrame, source_sha256: str) -> CleanBatch` in `src/football_stats/clean.py`: frozen dataclass `CleanBatch(leagues: pl.DataFrame, teams: pl.DataFrame, matches: pl.DataFrame)` where `matches` has every `ColumnSpec.db_column` (except the five key columns replaced by `league_code`, `match_date`, `home_team_key`, `away_team_key`), `source_sha256`, `source_row_number`, sorted by `source_row_number`; teams sorted by `team_key` so identity values are deterministic on a fresh DB (makes T025 pass)
- [x] T036 [US1] Implement `src/football_stats/load.py`: `LOCK_KEY: int` constant; `load_run(conn, batch: CleanBatch, excluded: pl.DataFrame, run: RunMeta) -> LoadCounts` executing research.md R11 steps 1–6 and 8 inside `with conn.transaction()`: `pg_try_advisory_xact_lock(LOCK_KEY)` (false → `ConcurrentRunError`); `CREATE TEMP TABLE stg_matches (…) ON COMMIT DROP`; `COPY stg_matches (<cols>) FROM STDIN (FORMAT csv)` fed from `batch.matches.select(<cols>).write_csv(include_header=False)`, where `<cols>` is one tuple generated from `COLUMN_SPECS` and used for both the `CREATE TEMP TABLE` column list and the COPY column list; `INSERT INTO leagues … ON CONFLICT DO NOTHING`; `INSERT INTO teams … ORDER BY team_key ON CONFLICT (team_key) DO UPDATE SET name = EXCLUDED.name WHERE teams.name IS DISTINCT FROM EXCLUDED.name`; `INSERT INTO matches … ORDER BY source_row_number ON CONFLICT ON CONSTRAINT matches_natural_key DO UPDATE SET <every non-key column> WHERE (<old>) IS DISTINCT FROM (<new>) RETURNING (xmax = 0)` → inserted/updated counts, unchanged = accepted − returned (add a code comment: `xmax = 0` identifies freshly inserted tuples — a widely used Postgres idiom relying on an implementation detail, covered by `test_second_run_reports_all_unchanged` and `test_changed_source_updates_match_in_place`); then `INSERT INTO ingest_runs (…all counts…, finished_at = clock_timestamp()) RETURNING run_id` (inserted last so its CHECKs hold with complete counts); column lists generated from `COLUMN_SPECS`; every `psycopg.Error` re-raised as `LoadError` after rollback; `RunMeta` (frozen dataclass: `started_at`, `source_url`, `source_sha256`, `source_bytes`, `raw_path`, `rows_read`, `rows_excluded`, `excluded_by_reason`) defined in this module (excluded-row persistence is added in T047) (makes T028 pass)
- [x] T037 [US1] Implement `src/football_stats/pipeline.py`: `run(settings: Settings, source_file: Path | None = None) -> RunReport` = `fetch_to_raw` → `read_header` → `check_header` (log extra columns as WARNING) → `check_structure` → `read_source` → `parse_frame` → `add_team_keys` → `partition` → `build_batch` → open psycopg connection (`psycopg.connect` wrapped so any `psycopg.Error`, e.g. `OperationalError` for a refused connection, becomes `LoadError`, exit 7) → `load_run`; frozen dataclass `RunReport` with the fields of contracts/run-report.schema.json and `to_json() -> str`; logs one INFO line per stage with row counts and a final `run_summary` line containing the JSON; nothing touches the DB before `load_run` (makes T026, T027 pass)
- [x] T038 [US1] Implement `src/football_stats/cli.py`: `main(argv: Sequence[str] | None = None) -> int` with argparse subcommand `ingest [--source-file PATH]`, logging configured to stderr (`%(asctime)s %(levelname)s %(name)s %(message)s`, level from settings), success → `sys.stdout.write(report.to_json() + "\n")`, exit 0; `FootballStatsError` → failure JSON `{"status":"failed","error":e.error,"message":str(e)}` and `e.exit_code`; any other `Exception` → logged with traceback, `unexpected_error`, exit 1; `if __name__ == "__main__": raise SystemExit(main())` (makes T029 pass)
- [x] T039 [US1] Run `scripts/check.sh` and fix until it exits 0

**Checkpoint**: US1 is functional and independently testable on fixtures (MVP).

---

## Phase 4: User Story 2 - Set up the database structure from scratch (Priority: P2)

**Goal**: A fresh machine with Docker gets a running database with the full structure from one
command; re-applying the structure is a no-op. (The migration itself and its tests were built in
Phase 2 because every story needs the schema.)

**Independent Test**: `scripts/smoke_compose.sh` passes on a machine with only Docker: `docker
compose up -d` → db healthy and `migrate` exited 0; `docker compose run --rm migrate` again exits 0
with no schema change; ingesting a mounted fixture through the container returns a valid report.

### Tests for User Story 2 (write first, must fail)

- [x] T040 [US2] Write executable `scripts/smoke_compose.sh` (`set -euo pipefail`; uses a throw-away project name `-p football_smoke` and a temporary `.env` generated from `.env.example` in which `POSTGRES_PORT` is set to a free port found with `python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'`, so the smoke run never collides with a dev stack or a local Postgres on 5432; `trap` runs `docker compose down -v` on exit): `docker compose up -d --wait db`; `docker compose run --rm migrate` (exit 0); capture `\dt` and `alembic_version` via `docker compose exec -T db psql`; `docker compose run --rm migrate` again and assert both captures are unchanged (US2-3); `docker compose run --rm -v "$PWD/tests/fixtures:/fixtures:ro" ingest football-stats ingest --source-file /fixtures/matches_valid.csv` and assert stdout JSON has `"status": "succeeded"` and `"rows_loaded": 12` (using `python3 -c` with `json`); no network access to the dataset

### Implementation for User Story 2

- [x] T041 [P] [US2] Write `Dockerfile`: `FROM python:3.12.11-slim-bookworm`; `pip install --no-cache-dir uv==0.12.5`; `WORKDIR /app`; copy `pyproject.toml uv.lock` then `uv sync --frozen --no-dev --no-install-project`; copy `src/ migrations/ alembic.ini`; `uv sync --frozen --no-dev`; `ENV PATH="/app/.venv/bin:$PATH" RAW_DATA_DIR=/app/data/raw`; create non-root user `app` and `mkdir -p /app/data/raw` owned by `app` (so the `rawdata` named volume is initialised writable); `USER app`; default `CMD ["football-stats", "ingest"]`
- [x] T042 [US2] Write `docker-compose.yml` per research.md R22: service `db` (`image: postgres:17.6-alpine`, env `POSTGRES_USER/PASSWORD/DB` from `.env`, healthcheck `pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB` interval 2s retries 30, volume `pgdata:/var/lib/postgresql/data`, ports `127.0.0.1:${POSTGRES_PORT:-5432}:5432`); service `migrate` (`build: .`, `command: ["alembic", "upgrade", "head"]`, `environment: DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}`, `depends_on: db: condition: service_healthy`, `restart: "no"`); service `ingest` (`build: .`, same `DATABASE_URL`, `SOURCE_URL`/`LOG_LEVEL` passed through when set, `volumes: ["rawdata:/app/data/raw"]`, `depends_on: migrate: condition: service_completed_successfully`, `profiles: ["ingest"]`, `restart: "no"`); named volumes `pgdata` and `rawdata`; no secrets inline (makes T040 pass)
- [x] T043 [US2] Extend `.github/workflows/ci.yml`: after `scripts/check.sh`, step `docker build -t football-stats:ci .`, then `scripts/smoke_compose.sh`
- [x] T044 [US2] Run `scripts/smoke_compose.sh` locally and `scripts/check.sh`; fix until both exit 0

**Checkpoint**: US1 and US2 work; the stack comes up with one command.

---

## Phase 5: User Story 3 - Understand what was excluded and why (Priority: P3)

**Goal**: Every excluded row is persisted with its original content, reason and run; re-running on
the same source changes nothing.

**Independent Test**: Ingest `tests/fixtures/matches_edge_cases.csv`; `excluded_rows` contains
exactly the rows marked excluded in `matches_edge_cases.expected.json`, with matching reason,
detail and raw content; a second run leaves `snapshot()` identical.

### Tests for User Story 3 (write first, must fail)

- [x] T045 [P] [US3] Write `tests/integration/test_excluded_rows.py`: `test_each_reason_persisted_with_raw_record` (for every excluded row in `matches_edge_cases.expected.json`: one `excluded_rows` row with the same `source_row_number`, `reason`, `detail`; `raw_record` equals the row re-read from the fixture with stdlib `csv` — untrimmed strings, empty → null; blank line → all-null object with all 48 keys), `test_excluded_rows_link_to_run` (`run_id` = the run's id; `source_sha256` = the file's sha), `test_duplicate_detail_points_to_kept_row` (the referenced row number exists in `matches.source_row_number`), `test_db_reconciles_with_run_counts` (parametrized `matches_edge_cases.csv`, `matches_real_sample.csv`: count of `matches` + count of `excluded_rows` with the run's `source_sha256` = the run's `rows_read`; SC-003 DB-level evidence)
- [x] T046 [P] [US3] Write `tests/integration/test_idempotency.py`: `test_second_run_leaves_identical_state` (parametrized over `matches_valid.csv`, `matches_edge_cases.csv`, `matches_real_sample.csv`: `snapshot()` of `leagues`, `teams`, `matches`, `excluded_rows` identical after run 2, including surrogate ids; `ingest_runs` has 2 rows with identical source and read/loaded/excluded columns; run 2 has inserted = updated = 0 and unchanged = rows_loaded), `test_second_run_reports_all_unchanged` (run 2 report: `matches_inserted` 0, `matches_updated` 0, `matches_unchanged` = `rows_loaded`), `test_changed_source_updates_match_in_place` (a copy of `matches_valid.csv` written to `tmp_path` with one row's `FTHome`/`FTAway`/`FTResult` changed → new file sha; run 2 report: `matches_inserted` 0, `matches_updated` 12 (provenance re-stamp, research.md R12), `matches_unchanged` 0; all `match_id`s unchanged; match count unchanged; the edited row has the new goals; every match has the new `source_sha256`)

### Implementation for User Story 3

- [x] T047 [US3] Extend `src/football_stats/load.py` `load_run` with research.md R11 step 7: `CREATE TEMP TABLE stg_excluded (source_row_number integer, reason text, detail text, raw_record jsonb) ON COMMIT DROP`; fill it with `cursor.copy("COPY stg_excluded (source_row_number, reason, detail, raw_record) FROM STDIN")` and `copy.write_row((n, reason, detail, psycopg.types.json.Jsonb(raw_record)))` per excluded row (no hand-built CSV/JSON quoting); `INSERT INTO excluded_rows (run_id, source_sha256, source_row_number, reason, detail, raw_record) SELECT … ON CONFLICT ON CONSTRAINT excluded_rows_source_key DO UPDATE SET reason = EXCLUDED.reason, detail = EXCLUDED.detail, raw_record = EXCLUDED.raw_record WHERE (excluded_rows.reason, excluded_rows.detail, excluded_rows.raw_record) IS DISTINCT FROM (EXCLUDED.reason, EXCLUDED.detail, EXCLUDED.raw_record)` — `run_id` is never updated (research.md R12)
- [x] T048 [US3] Update `src/football_stats/pipeline.py` to pass `partition.excluded` to `load_run` and log one INFO line per non-zero exclusion reason (`excluded reason=<code> count=<n>`) plus a DEBUG line per excluded row (`row=<n> reason=<code> detail=<detail>`) (makes T045, T046 pass)
- [x] T049 [US3] Run `scripts/check.sh` and fix until it exits 0

**Checkpoint**: All three user stories work independently.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: SC-004 verification, documentation, measured numbers.

- [x] T050 [P] Write `tests/integration/test_verify.py`: `test_every_loaded_match_equals_source` (after ingesting `matches_edge_cases.csv` and `matches_real_sample.csv`, re-read each fixture with stdlib `csv` and, for every row marked loaded, assert the DB row with that `source_row_number` has the same `match_date` (ISO string), `team_key` of home/away = `casefold(collapse_ws(strip(...)))` of the source, and `ft_home_goals`/`ft_away_goals` = `int(float(source))`), `test_verify_command_reports_no_differences` (`verify.run_verify(settings, sample=0, seed=42)` → 0 differences), `test_verify_detects_tampered_row` (UPDATE one match's `ft_home_goals` → `VerificationFailedError`, the difference names `source_row_number` and `ft_home_goals`), `test_verify_sample_is_seeded` (same seed → same row numbers checked), `test_verify_cli_report_matches_schema` (CLI `verify` stdout for both the success and the tampered case validates against `specs/001-match-data-ingestion/contracts/verify-report.schema.json`; exit 0 and 6; `RAW_DATA_DIR` = `tmp_path`)
- [x] T051 Implement `src/football_stats/verify.py` (`run_verify(settings, sample: int, seed: int, source_file: Path | None) -> VerifyReport`: locate latest `ingest_runs` row, open `RAW_DATA_DIR/matches-<sha>.csv` (or `source_file`), stdlib `csv` reader, choose rows not present in `excluded_rows` for that sha, sample with `random.Random(seed).sample(sorted(rows), k)` (`sample=0` → all), compare exactly per research.md R24 — date as ISO string, teams as `clean.team_key(source) == teams.team_key` (never display names), FT goals as integers, missing DB row as field `row` — raise `VerificationFailedError` carrying the report (with `message`, `checked`, `differences`) on differences) and add the `verify [--sample N] [--seed S] [--source-file PATH]` subcommand to `src/football_stats/cli.py` per contracts/cli.md (exit 6 on differences; JSON on stdout per `contracts/verify-report.schema.json`) (makes T050 pass)
- [x] T052 [P] Write `README.md`: project purpose; attribution "Gábor, A. (2025). *Club Football Match Data*. https://github.com/xgabora/Club-Football-Match-Data" (constitution) and MIT licence note; architecture diagram (extract → clean/validate → load; raw file → leagues/teams/matches/excluded_rows/ingest_runs); prerequisites; the two-command run (`cp .env.example .env`, `docker compose run --rm ingest`); quality gate (`scripts/check.sh`); design decisions and trade-offs (single transaction vs batches, upsert vs truncate-reload, content-addressed raw file, constraint parity, no season column) linking to `specs/001-match-data-ingestion/research.md`; "Known data quirks" section (stored as-is, out of scope for this feature: `King\u0092s Lynn` contains a C1 control character instead of an apostrophe and is the club's only spelling; 8 odds values of `0.0`; 2 `HandiSize` values of `-99.9`; blank line at file line 230,559; domain rules deferred to Phase 2); "Measured numbers" section with placeholders filled by T053
- [ ] T053 Execute `specs/001-match-data-ingestion/quickstart.md` §1–§10 against the real pinned dataset; record in `README.md` "Measured numbers" the SC-001 wall time, SC-002 load time, machine (CPU/RAM), and the real counts (rows read/loaded/excluded, leagues, teams); if any expected value in quickstart.md differs from reality, stop and report the difference instead of editing the expectation
- [x] T054 Run `scripts/check.sh` and `scripts/smoke_compose.sh` a final time; both exit 0

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none. T001 first (creates `pyproject.toml`, `uv.lock`, `scripts/check.sh`); T002, T003, T005 parallel after T001; T004 after T001; T006 last.
- **Foundational (Phase 2)**: after Phase 1. Tests T007, T008, T010, T011 in parallel; T009 (fixtures) parallel with them; T012 after T004. Implementation: T013 → T014; T015 after T013; T016 → T017 (needs T012 to run T010/T011); T018 last. **Blocks all user stories.**
- **US1 (Phase 3)**: after Phase 2. T019–T029 in parallel (different files). Implementation is sequential by data flow: T030 → T031 → T032 → T033 → T034 → T035 → T036 → T037 → T038 → T039.
- **US2 (Phase 4)**: after Phase 2 for the schema; T040's ingest step needs the CLI from T038, so in practice after US1. T041 ∥ T040; T042 after T041; T043 after T042; T044 last.
- **US3 (Phase 5)**: after US1 (extends `load.py` and `pipeline.py`). T045 ∥ T046; T047 → T048 → T049.
- **Polish (Phase 6)**: after US3 (T050 relies on excluded rows being persisted). T050 ∥ T052; T051 after T050; T053 after T051 and T052 and US2; T054 last.

### User Story Dependencies

- **US1 (P1)**: depends only on Foundational.
- **US2 (P2)**: schema part complete in Foundational; the container smoke test ingests a fixture, so it uses US1's CLI. Can be developed in parallel with US1 up to T043; T044 needs T038.
- **US3 (P3)**: builds on US1's loader (same files). Its tests are independent of US2.

### Within Each User Story

- Test tasks first; confirm they fail for the expected reason (ImportError/AttributeError or assertion), then implement.
- Data-flow order: extract → validate header → clean parsers → team normalization → partition → build batch → load → pipeline → CLI.
- Each phase ends with a `scripts/check.sh` run that must exit 0.

### Parallel Opportunities

- Phase 1: T002, T003, T005.
- Phase 2: T007, T008, T009, T010, T011, and T013.
- US1: all eleven test tasks T019–T029.
- US2: T040 and T041.
- US3: T045 and T046.
- Polish: T050 and T052.

---

## Parallel Example: User Story 1

```bash
# All US1 test files can be written at the same time (different files):
Task: "T019 tests/unit/test_extract_read.py"
Task: "T020 tests/unit/test_validate_header.py"
Task: "T021 tests/unit/test_clean_parsers.py"
Task: "T022 tests/unit/test_clean_team_names.py"
Task: "T023 tests/unit/test_validate_rows.py"
Task: "T024 tests/unit/test_accounting.py"
Task: "T025 tests/unit/test_clean_build.py"
Task: "T026 tests/integration/test_extract_download.py"
Task: "T027 tests/integration/test_ingest_pipeline.py"
Task: "T028 tests/integration/test_atomicity.py"
Task: "T029 tests/integration/test_cli.py"
```

## Parallel Example: User Story 3

```bash
Task: "T045 tests/integration/test_excluded_rows.py"
Task: "T046 tests/integration/test_idempotency.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 Setup → gate green on the empty skeleton.
2. Phase 2 Foundational → schema, contract, fixtures.
3. Phase 3 US1 → `football-stats ingest` loads fixture data correctly and atomically.
4. **STOP and VALIDATE**: `scripts/check.sh` green; run `uv run football-stats ingest` against a local database (quickstart §11).

### Incremental Delivery (each step is a committable milestone, Principle V)

1. Setup + Foundational → schema milestone.
2. US1 → ingestion MVP.
3. US2 → one-command containerized stack + CI image build and Compose smoke.
4. US3 → excluded rows persisted, idempotency proven.
5. Polish → SC-004 verify, README, measured numbers.

---

## Notes

- Total: 54 tasks — Setup 6, Foundational 12, US1 21, US2 5, US3 5, Polish 5.
- `[P]` = different files and no dependency on an incomplete task.
- Do not edit expected values in quickstart.md or fixtures' `.expected.json` to make a test pass; a mismatch is a finding to report.
- The orchestrator commits; tasks do not commit.
