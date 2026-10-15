---

description: "Task list for 002-data-contract"
---

# Tasks: Match Data Contract

**Input**: Design documents from `/specs/002-data-contract/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/data-contract.md,
contracts/run-report.schema.json, contracts/cli.md, quickstart.md

**Tests**: Required (constitution Principle III, planner rules). Every test task names the exact
test functions from plan.md "FR / SC → Test Mapping" and comes **before** the implementation task
that makes it pass. A test task is done when its tests exist, are collected, and fail for the
expected reason (ImportError/AttributeError for missing code, or an assertion). Exceptions are noted
on the task: some regression guards pass as soon as they are written. An implementation task is done
when its paired tests pass and `scripts/check.sh` exits 0.

**Existing codebase**: Feature 001 is implemented and green. `scripts/check.sh` already exists and is
**not modified** by this feature. Extend existing modules; do not rewrite them.

**Organization**: Setup → Foundational (test data only) → US1 (P1, MVP) → US2 (P2) → US3 (P2) →
US4 (P3) → Polish.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4 (user story phases only)

## Global rules for every task

- Python: full type hints (`mypy --strict`), google-style docstrings on public functions, `logging`
  only, no bare/blind `except`, no swallowed exceptions (001 rules unchanged).
- `data_contract.py`, `validate.py`, `clean.py` are pure: no clock (`date.today`, `datetime.now`),
  no DB, network or file access. The run date is always an argument (research.md R5).
- Tests: no external network; no timestamp assertions; no reliance on test order or on another
  test's data. Every test that ingests `matches_contract.csv`, or asserts a date boundary, passes
  `started_at=tests.stages.CONTRACT_STARTED_AT` to `pipeline.run` (or `run_date=RUN_DATE` to pure
  functions). Integration tests build `Settings` only through the `settings` or `make_settings`
  fixtures (raw files under `tmp_path`).
- Rule ids, constraint names, reason codes and detail strings are exactly as in data-model.md and
  contracts/data-contract.md; detail = violated rule ids in contract order joined by `", "`.
- The SQL of each `matches_contract_*` constraint is exactly data-model.md §1.1.

---

## Phase 1: Setup

**Purpose**: Add the new dependency and pin the library behaviour the design relies on. The gate
stays green.

- [x] T001 Add pandera: run `uv add "pandera[polars]>=0.34.1,<0.35"` so `pyproject.toml` `[project].dependencies` gains `"pandera[polars]>=0.34.1,<0.35"` and `uv.lock` is updated; confirm the lock resolves `pandera 0.34.1`, keeps `polars 1.44.2`, and contains no `pandas`/`numpy` (`grep -E '^name = "(pandas|numpy)"' uv.lock` prints nothing); `uv sync --frozen` succeeds
- [x] T002 Write `tests/unit/test_pandera_backend.py::test_pandera_polars_backend_behaviour` pinning research.md R2 facts on a tiny frame (`a: Int16 [0,1,2,3]`, `b: Int16 [1,1,1,1]`) with a DataFrame-level `pa.Check(fn, name="a_le_b")` where `fn(data: PolarsData)` returns `data.lazyframe.select((pl.col("a") <= pl.col("b")).alias("check_output"))`: (1) `validate(lazy=True)` raises `pa.errors.SchemaErrors` whose `failure_cases` is a `pl.DataFrame` containing columns `schema_context`, `check`, `index`; (2) the rows for the failing positions have `schema_context == "DataFrameSchema"`, `check == "a_le_b"`, `index == [2, 3]`; on `frame.slice(1)` the indices are `[1, 2]` (positions in the passed frame); (3) inside `pandera.config.config_context(validation_enabled=False)` no error is raised, but nesting `config_context(validation_enabled=True, validation_depth=ValidationDepth.SCHEMA_AND_DATA)` inside it raises again; (4) a schema declaring `a` as `pl.Int16` given an `Int64` column reports a failure case with `schema_context == "Column"` and a null `index`. (Passes immediately: it pins the library, not our code.)
- [x] T003 Run `scripts/check.sh`; it exits 0 (pandera added, nothing else changed)

**Checkpoint**: gate green with pandera locked.

---

## Phase 2: Foundational (shared test data and helpers)

**Purpose**: Test data and helpers that several stories use. No production code changes; the gate
stays green.

- [x] T004 [P] Repair `tests/fixtures/matches_edge_cases.csv` rows 19 and 21 (file lines 20 and 22, Twente v Ajax and ` TWENTE`/`AJAX `): change `HTHome,HTAway,HTResult` from `1.0,0.0,H` to `0.0,1.0,A`; leave `matches_edge_cases.expected.json` unchanged (research.md R17). Existing tests stay green
- [x] T005 [P] Create `tests/fixtures/matches_contract.csv` (real 48-column header, 23 data rows exactly as plan.md "Test Fixtures" table: base row B = data row 1 of `matches_valid.csv`, rows 1–16 `MatchDate` `2020-10-NN`, overrides per row; numeric values written like the source, e.g. `3.0`) and `tests/fixtures/matches_contract.expected.json` mapping every `source_row_number` (string key) to `"loaded"` or `{"reason": <code>, "detail": <exact detail>}` for contract version 1 (valid for any run date 2020-11-04 … 2998-12-31; tests use 2023-06-30) (the "v1 outcome" column); also add `tests/fixtures/matches_contract.pre.expected.json` with the same shape for the "pre outcome" column (contract version 0). Add a paragraph to `tests/fixtures/SOURCES.txt` stating the file is synthetic, one row per contract rule/edge case, and that the expected JSONs are keyed by 1-based `source_row_number`
- [x] T006 [P] Create `tests/contract_cases.py` (module docstring; pure test data, imports only `dataclasses` and `datetime`, nothing from `src`): `FULL_ROW: dict[str, str]` = base row B as the 48 source strings; frozen dataclass `ContractCase(case_id: str, overrides: dict[str, str | None], violated: tuple[str, ...])`; `CONTRACT_CASES: tuple[ContractCase, ...]` = exactly the 27 cases of plan.md "tests/contract_cases.py" table (same ids, overrides, `violated` in contract order); `materialize(case: ContractCase, run_date: dt.date) -> dict[str, str | None]` returning `{**FULL_ROW, **case.overrides}` with `MatchDate` `"{run_date}"` → `run_date.isoformat()` and `"{run_date+1}"` → `(run_date + timedelta(days=1)).isoformat()`; `CASE_IDS = [c.case_id for c in CONTRACT_CASES]` for `pytest.mark.parametrize(ids=...)`
- [x] T007 [P] Extend `tests/integration/helpers.py` with `content_snapshot(conn: psycopg.Connection) -> dict[str, list[object]]` per research.md R12, pure SQL: `leagues` → `SELECT league_code FROM leagues ORDER BY 1`; `teams` → `SELECT team_key, name FROM teams ORDER BY team_key`; `matches` → `SELECT (to_jsonb(m) - 'match_id' - 'home_team_id' - 'away_team_id') || jsonb_build_object('home_team_key', h.team_key, 'away_team_key', a.team_key) FROM matches m JOIN teams h ON h.team_id = m.home_team_id JOIN teams a ON a.team_id = m.away_team_id ORDER BY m.league_code, m.match_date, h.team_key, a.team_key`; `excluded_rows` → `SELECT to_jsonb(e) - 'excluded_row_id' - 'run_id' FROM excluded_rows e ORDER BY source_sha256, source_row_number`; and extend `tests/integration/conftest.py` with fixture `make_settings(tmp_path) -> Callable[[str], Settings]` returning `Settings(database_url=url, source_url="file:///nonexistent/override-in-test.csv", raw_data_dir=tmp_path / "raw", log_level="DEBUG")` for databases other than `db_url` (e.g. `empty_db_url`)
- [x] T008 Run `scripts/check.sh`; it exits 0

**Checkpoint**: test data ready; behaviour unchanged.

---

## Phase 3: User Story 1 — Only internally consistent matches are loaded (Priority: P1) 🎯 MVP

**Goal**: Every row that passed 001's checks is validated against contract version 1; violators are
excluded with `contract_violation` naming every rule; the report and `ingest_runs` record the
contract version and violations per rule.

**Independent Test**: ingest `matches_contract.csv` on a fresh database: only rows 1, 14, 15, 16, 18
are loaded, every other row is excluded with the exact expected reason/detail, read = loaded +
excluded, and the report/run record show version 1 and the per-rule counts of plan.md.

### Tests for User Story 1 (write first, expect failures)

- [ ] T009 [P] [US1] Update `tests/unit/test_source_contract.py::test_exclusion_reasons_in_precedence_order` to expect `missing_field, invalid_date, missing_result, invalid_value, same_team, contract_violation, duplicate`; create `tests/unit/test_errors.py::test_contract_schema_error_exit_code` (`ContractSchemaError` is a `FootballStatsError` with `exit_code == 9` and `error == "contract_schema_error"`) and `::test_clock_skew_error_exit_code` (`ClockSkewError`, `exit_code == 10`, `error == "clock_skew"`)
- [ ] T010 [US1] Update `tests/stages.py`: add `RUN_DATE = dt.date(2023, 6, 30)` and `CONTRACT_STARTED_AT = dt.datetime(2023, 6, 30, 12, 0, tzinfo=dt.UTC)` (past dates on/after the latest fixture date 2022-02-07, plan review P1-2, P2-1); add `tests/unit/test_data_contract.py::test_run_date_after_all_fixture_dates` (every parseable `MatchDate` in every `tests/fixtures/*.csv` except the synthetic `2999-01-01` future rows is ≤ `RUN_DATE` and `CONTRACT_STARTED_AT.date()`; feature-001 expectations MUST NOT be lowered); `partition_file(path, *, contract: DataContract = CURRENT_CONTRACT, run_date: dt.date = RUN_DATE)` and `partition_rows(rows, *, contract=CURRENT_CONTRACT, run_date=RUN_DATE)` forward both to `validate.partition(parsed, contract=contract, run_date=run_date)`; import `CURRENT_CONTRACT`, `DataContract` from `football_stats.data_contract`
- [ ] T011 [P] [US1] Write `tests/unit/test_data_contract.py`: `test_contract_rule_ids_stable` (`CONTRACT_VERSION == 1 == CURRENT_CONTRACT.version`; `CURRENT_CONTRACT.rule_ids == ("ht_le_ft_home", "ht_le_ft_away", "ft_result_consistent", "ht_result_consistent", "sot_le_shots_home", "sot_le_shots_away", "odds_min", "max_odds_ge_avg", "date_in_range")`; every id matches `^[a-z][a-z0-9_]*$` and has length ≤ 46; every description non-empty; every `rule.inputs` ⊂ `{s.db_column for s in COLUMN_SPECS}`; `odds_min` has 6 terms, `max_odds_ge_avg` 3, others 1), `test_schema_declares_rule_inputs_with_parser_dtypes` (`build_schema(CURRENT_CONTRACT, RUN_DATE).columns` keys == union of rule inputs; dtypes: goals/shots/targets `Int16`, odds `Float64`, results `String`, `match_date` `Date`; `ft_*` and `match_date` not nullable, the rest nullable; one check per rule with `name == rule_id`), `test_pre_contract_is_version_zero_without_rules`, `test_contract_case_outcome[case]` (parametrized over `CONTRACT_CASES` with `ids=CASE_IDS`: `partition_rows([materialize(case, RUN_DATE)])` → if `case.violated` is empty the row is accepted, else exactly one excluded row with `reason == "contract_violation"` and `detail == ", ".join(case.violated)`), `test_date_in_range_uses_injected_run_date` (a row dated 2026-10-15: accepted with `run_date=2026-10-15`, excluded `date_in_range` with `run_date=2026-10-14`), `test_find_violations_sorted_long_format` (three rows given in descending `source_row_number` with violations `{3: multi_rule, 2: none, 1: max_below_avg}` → frame with columns `source_row_number` (UInt32), `rule_id` (String) equal to `[(1, "max_odds_ge_avg"), (3, "ht_le_ft_home"), (3, "ft_result_consistent")]`), `test_find_violations_empty_contract_or_frame` (`PRE_CONTRACT` → empty frame with the two columns; zero-row frame → empty), `test_find_violations_ignores_disabled_pandera_config` (call inside `pandera.config.config_context(validation_enabled=False)` → still returns the violation of a `ft_result_wrong` row), `test_find_violations_wrong_dtype_raises_contract_schema_error` (cast `ft_home_goals` to `Int64` → `ContractSchemaError`), `test_find_violations_raising_rule_raises_contract_schema_error` (a `DataContract(98, (ContractRule("broken", "test only", (Term(("ft_home_goals",), lambda _d: pl.col("not_an_input") > 0),)),))` whose condition reads a column outside its inputs → `ContractSchemaError`; research.md R2 fact 7), `test_find_violations_is_complete_for_many_failures` (50,000 rows of case `ft_result_wrong` built with `pl.concat` and a fresh `source_row_number` 1…50,000 → exactly 50,000 violation rows, one per row number), `test_find_violations_ignores_pandera_env_disable` (`subprocess.run([sys.executable, "-c", <script>], env={"PATH": ..., "PYTHONPATH": "<repo>/src:<repo>", "PANDERA_VALIDATION_ENABLED": "False", "PANDERA_VALIDATION_DEPTH": "SCHEMA_ONLY"}, timeout=60)` where the script imports `football_stats.data_contract` **after** the env is set, runs `find_violations` on one `ft_result_wrong` row, and prints the rule ids; assert stdout is `ft_result_consistent` and exit 0; no sockets are used) Build frames for `find_violations` with `tests.stages.parsed_frame`-style parsing (`clean.add_team_keys(clean.parse_frame(raw))`) so dtypes are the real ones
- [ ] T012 [P] [US1] Write `tests/unit/test_validate_contract.py`: `test_contract_fixture_outcomes_match_expected` (`partition_file(matches_contract.csv)` outcomes == `matches_contract.expected.json`, using the `outcomes` helper style of `test_validate_rows.py`; and with `contract=PRE_CONTRACT` == `matches_contract.pre.expected.json`), `test_multi_rule_row_detail_lists_all_rules_in_contract_order` (case `multi_rule` → `"ht_le_ft_home, ft_result_consistent"`), `test_per_column_failure_takes_precedence_over_contract` (`HomeShots` `-1` + `FTResult` `A` → `invalid_value` / `HomeShots`), `test_same_team_takes_precedence_over_contract` (`AwayTeam` ` arsenal` + `FTResult` A → `same_team` / `arsenal`), `test_contract_checked_before_duplicates` (rows `[{FTResult: D}, {}]` with the same key → row 1 `contract_violation` `ft_result_consistent`, row 2 accepted, no `duplicate`), `test_invalid_later_copy_is_contract_violation_not_duplicate` (rows spelled out as `[{**FULL_ROW}, {**FULL_ROW, "AwayTarget": "10.0"}]`; with `partition_rows`' own base row the shots are empty and the rule would be skipped → row 2 `contract_violation` `sot_le_shots_away`, not `duplicate`), `test_accepted_rows_satisfy_contract[name]` (for `matches_valid.csv`, `matches_edge_cases.csv`, `matches_real_sample.csv`, `matches_contract.csv`: `find_violations(part.accepted, CURRENT_CONTRACT, RUN_DATE)` is empty)
- [ ] T013 [P] [US1] Update `tests/unit/test_accounting.py`: add `("matches_contract.csv", 23)` to `test_partition_accounts_for_every_row`; `test_report_counts_per_reason` expects all seven codes; new `test_violations_by_rule_counts_each_rule_once_per_row` (`partition_file(matches_contract.csv).violations_by_rule()` == the dict in plan.md "Derived expectations" with keys in contract order (assert `list(d) == list(CURRENT_CONTRACT.rule_ids)`); `counts_by_reason()["contract_violation"] == 15`; `sum(values) == 16`; for `matches_valid.csv` every count is 0 and all nine keys are present); new `test_report_invariants_hold[name]` over all accepted-header fixtures (`matches_valid.csv`, `matches_edge_cases.csv`, `matches_real_sample.csv`, `matches_contract.csv`, `matches_header_only.csv`): `counts_by_reason()["contract_violation"] <= sum(violations_by_rule().values())`, both zero together, `sum(counts_by_reason().values()) == excluded.height`, `accepted.height + excluded.height == rows_read`
- [ ] T014 [P] [US1] Update integration schema tests for revision `0002_contract_run_metadata`: in `tests/integration/test_migrations.py` extend `test_upgrade_creates_schema` (`ingest_runs` has `contract_version integer NOT NULL`, `violations_by_rule jsonb NOT NULL`, `matches_removed integer NOT NULL`, `excluded_rows_removed integer NOT NULL`, all with `column_default IS NULL`; `pg_get_constraintdef` of `excluded_rows_reason_check` contains `'contract_violation'`), change `test_upgrade_is_noop_at_head` to expect `[("0002_contract_run_metadata",)]`, add `test_upgrade_backfills_existing_runs` (`empty_db_url` → `helpers.upgrade(url, "0001_initial_schema")` → insert one balanced `ingest_runs` row with plain SQL (0001 columns) → `helpers.upgrade(url)` → that row has `contract_version 0`, `violations_by_rule {}`, `matches_removed 0`, `excluded_rows_removed 0`); in `tests/integration/test_constraints.py` make `_run` supply `contract_version 1`, `violations_by_rule '{}'::jsonb`, `matches_removed 0`, `excluded_rows_removed 0`, extend `test_excluded_rows_reason_check` (reason `contract_violation` accepted), add `test_ingest_runs_contract_columns_checks` (`contract_version -1`, `violations_by_rule '[]'::jsonb`, `matches_removed -1`, `excluded_rows_removed -1` each → `CheckViolation`; omitting `contract_version` → `NotNullViolation`)
- [ ] T015 [P] [US1] Write `tests/integration/test_contract_pipeline.py` (US1 part): `test_contract_fixture_loads_only_consistent_rows` (`pipeline.run(settings, source_file=matches_contract.csv, started_at=CONTRACT_STARTED_AT)`; `SELECT source_row_number FROM matches ORDER BY 1` == `[1, 14, 15, 16, 18]`; `excluded_rows` `(source_row_number, reason, detail)` == `matches_contract.expected.json` for every non-loaded row; `rows_read 23 == rows_loaded 5 + rows_excluded 18`; leagues `["E0"]`; teams `(team_key, name)` == `[("arsenal","Arsenal"), ("chelsea","Chelsea"), ("leeds","leeds"), ("wolves","WOLVES")]`), `test_run_report_and_record_contract_metadata` (report `contract_version == 1`, `violations_by_rule` == plan.md dict (all nine keys, zeros included), `excluded_by_reason["contract_violation"] == 15`, `matches_removed == excluded_rows_removed == 0`; the stored `ingest_runs` row has the same `contract_version`, `violations_by_rule`, `excluded_by_reason`, `matches_removed`, `excluded_rows_removed`), `test_date_in_range_boundary_uses_run_start_date` (write a 2-row CSV to `tmp_path` from `FULL_ROW` with `MatchDate` `2021-06-30` and `2021-07-01` (different keys); run with `started_at=dt.datetime(2021, 7, 1, 1, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))` (= 2021-06-30 23:00 UTC) → first row loaded, second excluded `date_in_range`; past dates so the real DB clock is irrelevant); in `test_run_report_and_record_contract_metadata` also use `caplog` (`caplog.set_level(logging.INFO, logger="football_stats")`) to assert exactly nine `contract rule=<id> violations=<n>` messages with the plan.md counts (zeros included); add `tests/integration/test_atomicity.py::test_db_clock_behind_run_date_fails_before_writes` (preload `matches_valid.csv`; `before = helpers.snapshot`; call `load.load_run` with a valid batch and `RunMeta(run_date=dt.date(2999, 1, 1), ...)` → `ClockSkewError` whose message names both dates; snapshot unchanged)
- [ ] T016 [P] [US1] Update existing integration tests for the new run record and report: `tests/integration/helpers.py::snapshot` `ingest_runs` query selects `run_id, source_url, source_sha256, source_bytes, raw_path, rows_read, rows_loaded, rows_excluded, excluded_by_reason, contract_version, violations_by_rule, matches_inserted, matches_updated, matches_unchanged, matches_removed, excluded_rows_removed`; `tests/integration/test_idempotency.py::test_second_run_leaves_identical_state` gains param `matches_contract.csv` (run with `started_at=CONTRACT_STARTED_AT`) and asserts `run_one[1:11] == run_two[1:11]` and `run_two[11:] == (0, 0, run_two[6], 0, 0)`; `tests/integration/test_ingest_pipeline.py::test_ingest_report_counts` `by_reason` includes `contract_violation` (0); `tests/integration/test_cli.py` loads `SCHEMA` from `specs/002-data-contract/contracts/run-report.schema.json` and `test_cli_ingest_report_matches_schema` additionally asserts the contracts/cli.md invariants on the report (`rows_excluded == sum(excluded_by_reason)`, `excluded_by_reason["contract_violation"] <= sum(violations_by_rule)`, both 0 together, `contract_version == CONTRACT_VERSION`); `tests/integration/test_atomicity.py::_run_meta` passes `run_date=RUN_DATE`, `contract_version=1` and `violations_by_rule={rule_id: 0 for rule_id in CURRENT_CONTRACT.rule_ids}`; `tests/integration/test_excluded_rows.py::test_db_reconciles_with_run_counts` gains param `matches_contract.csv`

### Implementation for User Story 1

- [ ] T017 [US1] In `src/football_stats/source_contract.py` add `ExclusionReason.CONTRACT_VIOLATION = "contract_violation"` between `SAME_TEAM` and `DUPLICATE` (docstring: precedence order, research.md R4); in `src/football_stats/errors.py` add `ContractSchemaError(FootballStatsError)` with `exit_code = 9`, `error = "contract_schema_error"`, and `ClockSkewError(FootballStatsError)` with `exit_code = 10`, `error = "clock_skew"`, each with a docstring (makes T009 pass)
- [ ] T018 [US1] Create `src/football_stats/data_contract.py` per data-model.md §2 and research.md R2, R3, R6: frozen dataclasses `Term(inputs: tuple[str, ...], condition: Callable[[dt.date], pl.Expr])`, `ContractRule(rule_id, description, terms)` with property `inputs` and method `passes(run_date) -> pl.Expr` = `pl.all_horizontal([pl.any_horizontal([pl.col(c).is_null() for c in t.inputs]) | t.condition(run_date) for t in terms])`, `DataContract(version: int, rules: tuple[ContractRule, ...])` with property `rule_ids`; `CONTRACT_VERSION = 1`; the nine rules in contract order with the exact conditions of research.md R3 (result rule: `pl.when(h > a).then(pl.lit("H")).when(h == a).then(pl.lit("D")).otherwise(pl.lit("A")) == result`; `date_in_range`: `(pl.col("match_date") >= dt.date(2000, 1, 1)) & (pl.col("match_date") <= pl.lit(run_date))`); `CURRENT_CONTRACT`, `PRE_CONTRACT = DataContract(0, ())`; `build_schema(contract, run_date) -> pa.DataFrameSchema` (columns from `COLUMN_SPECS` parser → dtype `COUNT: pl.Int16`, `DECIMAL: pl.Float64`, `RESULT: pl.String`, `DATE: pl.Date`; `nullable = spec.kind is ColumnKind.OPTIONAL`; one DataFrame-level `pa.Check(fn, name=rule.rule_id)` per rule, no `error=`; `strict=False`, `coerce=False`); `find_violations(frame, contract, run_date) -> pl.DataFrame` (empty contract or frame → empty frame with schema `{"source_row_number": pl.UInt32, "rule_id": pl.String}`; else select `source_row_number` + inputs, `validate(lazy=True)` inside `config_context(validation_enabled=True, validation_depth=ValidationDepth.SCHEMA_AND_DATA)`; on `SchemaErrors` raise `ContractSchemaError` naming the offending row of `failure_cases` if any has `schema_context != "DataFrameSchema"`, `check` not in `rule_ids`, or null `index`; otherwise map `index` → `source_row_number` by position, sort by `source_row_number` then rule position) (makes T011 pass)
- [ ] T019 [US1] Update `src/football_stats/validate.py`: `partition(parsed, *, contract: DataContract, run_date: dt.date) -> Partition`; after computing reasons 1–5, call `find_violations` on the rows with no reason; rows with ≥ 1 violation get `reason = "contract_violation"` and `detail` = rule ids in contract order joined by `", "` (sort long frame by `source_row_number` and rule position, `group_by(..., maintain_order=True)`, `str.join(", ")`); duplicate detection runs on the remaining rows only (data-model.md §3); `Partition` gains fields `violations: pl.DataFrame` and `rule_ids: tuple[str, ...]` and method `violations_by_rule() -> dict[str, int]` (every rule id in contract order, zeros included); update the module/partition docstrings to the seven-step precedence (makes T010, T012, T013 pass; 001 unit tests stay green)
- [ ] T020 [US1] Create `migrations/versions/0002_contract_run_metadata.py` (`revision = "0002_contract_run_metadata"`, `down_revision = "0001_initial_schema"`, plain `op.execute` SQL): `ALTER TABLE ingest_runs ADD COLUMN contract_version integer NOT NULL DEFAULT 0 CHECK (contract_version >= 0)`, `ADD COLUMN violations_by_rule jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(violations_by_rule) = 'object')`, `ADD COLUMN matches_removed integer NOT NULL DEFAULT 0 CHECK (matches_removed >= 0)`, `ADD COLUMN excluded_rows_removed integer NOT NULL DEFAULT 0 CHECK (excluded_rows_removed >= 0)`, then `ALTER COLUMN … DROP DEFAULT` for all four; `ALTER TABLE excluded_rows DROP CONSTRAINT excluded_rows_reason_check` and re-add it with the seven codes of data-model.md §1.3; `downgrade()` reverses (restores the six-code CHECK validated, drops the four columns) (makes T014 pass)
- [ ] T021 [US1] Update `src/football_stats/load.py` and `src/football_stats/pipeline.py`: `RunMeta` gains `run_date: dt.date`, `contract_version: int`, `violations_by_rule: dict[str, int]`; in `load_run`, right after the advisory lock and before any write, `SELECT (now() AT TIME ZONE 'UTC')::date` and raise `ClockSkewError(f"database UTC date {db} is earlier than run date {run}")` if it is earlier than `run.run_date` (research.md R8); `LoadCounts` gains `matches_removed: int`, `excluded_rows_removed: int` (0 until US3); the `ingest_runs` INSERT writes `contract_version`, `violations_by_rule` (as `Jsonb`), `matches_removed`, `excluded_rows_removed`; `pipeline.run(settings, source_file=None, *, contract: DataContract = CURRENT_CONTRACT, started_at: dt.datetime | None = None)` (default `dt.datetime.now(dt.UTC)`; raise `ValueError` if naive; `run_date = started_at.astimezone(dt.UTC).date()`) passes `contract`/`run_date` to `validate.partition` and `started_at` to `RunMeta`; `RunReport` gains `contract_version`, `violations_by_rule`, `matches_removed`, `excluded_rows_removed` in the field order of contracts/run-report.schema.json; log `contract rule=<id> violations=<n>` at INFO for every rule (makes T015, T016 pass)
- [ ] T022 [US1] Run `scripts/check.sh`; it exits 0

**Checkpoint**: US1 delivered: the pipeline enforces contract v1, reports and stores it (MVP).

---

## Phase 4: User Story 2 — The database itself enforces the contract (Priority: P2)

**Goal**: Nine `NOT VALID` CHECK constraints reject any new or updated violating match; the
migration succeeds on a database that already contains violators; parity with the pipeline is
proven rule by rule.

**Independent Test**: on a migrated database, a direct insert for each violating case is rejected
and each valid case accepted; upgrading a seeded feature-001 database with violators succeeds and
re-applying is a no-op.

### Tests for User Story 2 (write first, expect failures)

- [ ] T023 [US2] Add to `tests/integration/helpers.py` `seed_pre_contract(database_url: str, source: Path, run_date: dt.date) -> str` per research.md R13: `upgrade(database_url, "0001_initial_schema")`; `part = validate.partition(clean.add_team_keys(clean.parse_frame(extract.read_source(source))), contract=PRE_CONTRACT, run_date=run_date)`; `sha = hashlib.sha256(source.read_bytes()).hexdigest()`; `batch = clean.build_batch(part.accepted, sha)`; in one transaction with plain SQL: insert leagues (sorted), teams (`ORDER BY team_key`), matches (in `source_row_number` order; team ids via `(SELECT team_id FROM teams WHERE team_key = %s)`; every non-key column of `MATCH_STAGING_COLUMNS`), one `ingest_runs` row with only 0001 columns (`rows_loaded = matches_inserted = batch.matches.height`, `matches_updated = matches_unchanged = 0`, `excluded_by_reason = part.counts_by_reason()` with the `contract_violation` key removed), and the excluded rows (`raw_record` via `Jsonb`); return `sha`
- [ ] T024 [P] [US2] Extend `tests/integration/test_constraints.py`: `test_db_agrees_with_pipeline_on_contract_case[case]` (parametrized over `CONTRACT_CASES`, `ids=CASE_IDS`; seed league `E0`, teams `arsenal` (1) and `chelsea` (2); read `run_date = SELECT (now() AT TIME ZONE 'UTC')::date`; build the staged row from `partition_rows([materialize(case, run_date)], contract=PRE_CONTRACT, run_date=run_date)` + `clean.build_batch`; copy it into `CREATE TEMP TABLE probe (LIKE matches INCLUDING DEFAULTS INCLUDING IDENTITY)` (no CHECKs copied; identity copied so inserts omitting `match_id` work, plan review P2-3) and, for every constraint from `SELECT conname, pg_get_expr(conbin, conrelid) FROM pg_constraint WHERE conrelid = 'matches'::regclass AND conname LIKE 'matches_contract_%'`, evaluate `SELECT (<expr>) IS NOT FALSE FROM probe` — assert the set of failing constraint names == `{f"matches_contract_{r}" for r in case.violated}` (all of them, not just the first one PostgreSQL would report, plan review P1-3); then INSERT the row into `matches` inside a savepoint in the same transaction as the date query and assert it succeeds iff `case.violated` is empty (else `psycopg.errors.CheckViolation`); also assert the pipeline verdict `partition_rows([materialize(case, run_date)], run_date=run_date)` agrees (accepted iff the DB accepted)), `test_db_constraints_agree_with_pipeline_on_fixture_rows[name]` (params `matches_contract.csv`, `matches_edge_cases.csv`, `matches_real_sample.csv`: parse with `PRE_CONTRACT`, COPY every accepted staged row into the `probe` temp table, evaluate each constraint expression per row as above, and assert the per-row failing set equals `find_violations(part.accepted, CURRENT_CONTRACT, run_date)` grouped by row, with `run_date` = the database's UTC date), `test_contract_cases_cover_every_rule` (the union of `case.violated` over `CONTRACT_CASES` == `set(CURRENT_CONTRACT.rule_ids)`, and every rule has ≥ 1 case with that rule as the only violation), `test_contract_constraint_rejects_violating_update` (insert `valid_full`, then `UPDATE matches SET ht_home_goals = 9` → `CheckViolation` on `matches_contract_ht_le_ft_home`; `UPDATE matches SET max_odd_home = 1.0` → `CheckViolation` on `matches_contract_max_odds_ge_avg`)
- [ ] T025 [P] [US2] Extend `tests/integration/test_migrations.py`: `test_upgrade_creates_schema` also asserts the nine constraints `matches_contract_<id>` exist on `matches` with `contype = 'c'`, `convalidated = false`, and `pg_get_constraintdef` ending in `NOT VALID`; `test_upgrade_is_noop_at_head` expects `[("0003_contract_constraints",)]`; new `test_every_contract_rule_has_db_constraint` (`{conname[len("matches_contract_"):] for conname LIKE 'matches_contract_%'}` == `set(CURRENT_CONTRACT.rule_ids)`); new `test_upgrade_succeeds_with_violating_matches` (`empty_db_url`; `seed_pre_contract(url, matches_contract.csv, RUN_DATE)`; `helpers.upgrade(url)` succeeds; `matches` still has 18 rows incl. `source_row_number` 2–13 and 22; a direct INSERT of case `ht_home_gt_ft` (new key) → `CheckViolation`; a second `helpers.upgrade(url)` leaves `catalog_snapshot` and `alembic_version` unchanged); `test_downgrade_then_upgrade_roundtrip` unchanged (must stay green through 0003 → 0002 → 0001 → base and back); new `test_downgrade_refused_with_contract_violation_rows` (`db_url` + `pipeline.run(settings, matches_contract.csv, started_at=CONTRACT_STARTED_AT)`; `helpers.downgrade(db_url, "0001_initial_schema")` raises; because `env.py` commits per revision, `alembic_version` is then `0002_contract_run_metadata`, no `matches_contract_*` constraint exists, the four `ingest_runs` columns and the seven-code reason CHECK are still present, and all 15 `contract_violation` rows are still in `excluded_rows` (research.md R18))
- [ ] T026 [P] [US2] Extend `tests/integration/test_contract_pipeline.py`: `test_no_loaded_match_violates_contract` (ingest `matches_contract.csv` (`started_at=CONTRACT_STARTED_AT`) and `matches_real_sample.csv`; then inside one transaction that is **rolled back** at the end, `ALTER TABLE matches VALIDATE CONSTRAINT matches_contract_<id>` succeeds for each rule id — SC-001 using the database's own rule; afterwards assert every `matches_contract_*` still has `convalidated = false` (read-only proof, research.md R8)), `test_upsert_rewrites_existing_violating_match` (`empty_db_url` + `make_settings`; `seed_pre_contract(url, matches_contract.csv, RUN_DATE)`; `helpers.upgrade(url)`; `pipeline.run(make_settings(url), source_file=matches_contract.csv, started_at=CONTRACT_STARTED_AT)` succeeds; the match with key (E0, 2020-11-01, leeds, wolves) now has `ft_result = 'H'` and `source_row_number = 18`; research.md R9. Note: this guard may already pass before T027; it protects R9 once the constraints exist)

### Implementation for User Story 2

- [ ] T027 [US2] Create `migrations/versions/0003_contract_constraints.py` (`revision = "0003_contract_constraints"`, `down_revision = "0002_contract_run_metadata"`): nine `ALTER TABLE matches ADD CONSTRAINT matches_contract_<id> CHECK (<expr>) NOT VALID` with the exact names and expressions of data-model.md §1.1, in contract order; `downgrade()` drops the nine constraints (makes T024–T026 pass)
- [ ] T028 [US2] Run `scripts/check.sh`; it exits 0

**Checkpoint**: US2 delivered: storage-level enforcement with proven parity; upgrade path safe.

---

## Phase 5: User Story 3 — Re-runs converge to a fresh-run state (Priority: P2)

**Goal**: Re-running on the same source file removes this file's matches that are no longer
accepted, removes stale exclusion records, removes orphan teams/leagues, all inside the single
transaction, so the database equals a fresh build.

**Independent Test**: seed a feature-001 database from `matches_contract.csv`, upgrade, re-run;
its `content_snapshot` equals that of a fresh database loaded once with the contract.

### Tests for User Story 3 (write first, expect failures)

- [ ] T029 [P] [US3] Write `tests/integration/test_convergence.py` (all runs use `started_at=CONTRACT_STARTED_AT`; upgraded DB = `empty_db_url` + `seed_pre_contract(url, source, RUN_DATE)` + `helpers.upgrade(url)` + `pipeline.run(make_settings(url), source_file=source, ...)`; fresh DB = `db_url` + one `pipeline.run(settings, ...)`): `test_upgrade_then_rerun_equals_fresh_build[name]` (params `matches_contract.csv`, `matches_edge_cases.csv`, `matches_real_sample.csv`; `helpers.content_snapshot(upgraded) == helpers.content_snapshot(fresh)` for all four tables, plus `VALIDATE CONSTRAINT` of all nine succeeds on the upgraded DB inside a rolled-back transaction), `test_rerun_removes_now_violating_matches` (`matches_contract.csv`: report `matches_inserted 0`, `matches_updated 1`, `matches_unchanged 4`, `matches_removed 13`; no match has `source_row_number` in 2–13 or 22; those rows are in `excluded_rows` with `reason = 'contract_violation'` and the expected detail; the `ingest_runs` row stores `matches_removed 13`; `caplog` (logger `football_stats`, INFO) contains `converge matches_removed=13 excluded_rows_removed=1 teams_removed=1 leagues_removed=1`), `test_rerun_loads_rows_whose_exclusion_no_longer_applies` (`matches_contract.csv` upgraded DB: `excluded_rows_removed == 1`, no excluded row 18, match row 18 present), `test_reload_after_loosened_contract_equals_fresh_build` (two databases, so no third-database fixture is needed (plan review P1-5): on `db_url` run `matches_valid.csv` with a test-only contract `DataContract(99, CURRENT_CONTRACT.rules + (ContractRule("test_no_draws", "test only: no draws", (Term(("ft_result",), lambda _d: pl.col("ft_result") != "D"),)),))` → draw rows excluded `contract_violation` / `test_no_draws`; then run with `CURRENT_CONTRACT` → those rows loaded, their exclusion records gone, `excluded_rows_removed` == their count; on `empty_db_url` + `helpers.upgrade` + `make_settings` run `matches_valid.csv` once with `CURRENT_CONTRACT`; the two `content_snapshot`s are equal), `test_orphan_team_and_league_removed` (seeded DB has team `ghost fc` and league `X1`; after the re-run neither exists; `teams` names for `leeds`/`wolves` are `leeds`/`WOLVES`), `test_converged_rerun_is_noop` (after the converging run, a third run leaves `helpers.snapshot` of `leagues`, `teams`, `matches`, `excluded_rows` identical including ids; report `matches_inserted == matches_updated == matches_removed == excluded_rows_removed == 0`), `test_convergence_scoped_to_source_file` (seed with `matches_contract.csv`, upgrade, then ingest `matches_valid.csv` (different sha): run succeeds, `matches_removed 0`, the 13 violating matches of the seeded file are still present)
- [ ] T030 [P] [US3] Add `tests/integration/test_atomicity.py::test_failure_after_convergence_rolls_back` (seed + upgrade `matches_contract.csv` on `empty_db_url`; `before = helpers.snapshot` (after upgrade); compute `part = partition_file(matches_contract.csv)` and batch with the seeded sha; replace the reason of one excluded row by `'bogus'`; build `meta` consistent with `part` (`rows_read 23`, `rows_excluded 18`, `excluded_by_reason = part.counts_by_reason()`, `run_date=RUN_DATE`, `contract_version 1`, `violations_by_rule = part.violations_by_rule()`, the seeded sha) so the `ingest_runs` CHECKs pass; `load.load_run(conn, batch, excluded, meta)` raises `LoadError` (the excluded-row upsert, the last step, violates `excluded_rows_reason_check` after the convergence deletes ran); `helpers.snapshot` == `before`, i.e. the 13 violating matches are still there; **positive control** in the same test (plan review P1-6): call `load.load_run` again on the same connection with the unmodified excluded frame and assert it returns `matches_removed == 13` and `excluded_rows_removed == 1` and the 13 matches are gone. The positive control fails until T031, so the test is red-then-green, and it proves that the deletes run before the failing step and are rolled back)

### Implementation for User Story 3

- [ ] T031 [US3] Implement convergence in `src/football_stats/load.py` per data-model.md §5 and research.md R10, R11: create and COPY `stg_excluded` together with the other staging tables (step 2), then `ANALYZE stg_matches, stg_excluded` so the anti-joins are planned from real row counts (research.md R15); after the matches upsert run the stale-match DELETE of research.md R10 (`rowcount` → `matches_removed`), then `DELETE FROM excluded_rows e WHERE e.source_sha256 = %s AND NOT EXISTS (SELECT 1 FROM stg_excluded s WHERE s.source_row_number = e.source_row_number)` (→ `excluded_rows_removed`), then the orphan team and league DELETEs of research.md R11 (counts logged as `converge matches_removed=%d excluded_rows_removed=%d teams_removed=%d leagues_removed=%d`); then insert `ingest_runs` with the real counters; then the unchanged excluded-row upsert; keep the matches upsert SET list = all non-key columns (research.md R9) and add a comment saying why; `pipeline.run` copies the counters into `RunReport` (makes T029, T030 pass)
- [ ] T032 [US3] Run `scripts/check.sh`; it exits 0

**Checkpoint**: US3 delivered: contract changes converge on re-run; FR-009/SC-004 proven.

---

## Phase 6: User Story 4 — Know how the real dataset measures up (Priority: P3)

**Goal**: README documents the contract and real per-rule numbers; decisions recorded.

**Independent Test**: README names version 1 and all nine rules with a measured per-rule table;
`ingest_runs` shows the version for every run.

- [ ] T033 [P] [US4] Write `tests/unit/test_readme_contract.py::test_readme_documents_every_rule` (reads `README.md` from the repo root; asserts it contains a "Data contract" heading, the text `contract version {CONTRACT_VERSION}` (case-insensitive), and every `CURRENT_CONTRACT.rule_ids` entry in backticks)
- [ ] T034 [US4] Update `README.md`: new "Data contract" section (version 1, the nine rules with one-line descriptions as in contracts/data-contract.md, precedence now seven reasons, link to `specs/002-data-contract/`); architecture diagram adds `data_contract.py (pandera)` between validate and load and migrations `0001–0003`; modules list adds `data_contract`; design decisions table adds rows: pandera evaluates DataFrame-level checks vectorized (R2), `NOT VALID` constraints + parity (R8), convergence on re-run of the same file (R10), orphan team/league deletion (R11), "identical" = content without surrogate ids (R12); "Known data quirks" updated (0.0 odds and max-below-average odds are now excluded by the contract); "Measured numbers — data contract" table whose every unmeasured cell is the literal placeholder `TBD-T036`. The README must **not** advise running `VALIDATE CONSTRAINT` by hand; if it mentions validation, state that it is only ever run inside a rolled-back transaction as a read-only check, and that persistent validation would ship as a future Alembic revision (research.md R8, plan review P1-1) (makes T033 pass)
- [ ] T035 [US4] Run `scripts/check.sh`; it exits 0
- [ ] T036 [US4] **(manual, network)** Execute `specs/002-data-contract/quickstart.md` §1–§7 against the real pinned dataset (starting from the feature-001 database; rebuild images with `docker compose build`). Record in `README.md` "Measured numbers — data contract": run date, contract version, per-rule violation count and share of rows read (all nine rules), rows read/loaded/excluded, `matches_removed` of the converging re-run, wall time and `duration_seconds` of the fresh full run (SC-005 < 5 min), machine. Append to `specs/002-data-contract/decisions.md` a section `## FR-011 real-data review` listing every rule's measured share, and for each rule above 1 % a review entry (keep / adjust / drop, with reasoning); add an observation entry for `max_odds_ge_avg` (largest share) either way. If any measured value differs from quickstart.md / research.md R16, stop and report the difference instead of editing the expectation

**Checkpoint**: US4 delivered; FR-011, FR-012, SC-005, SC-006 measured and documented.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T037 Confirm `grep -n "TBD-T036" README.md` prints nothing (FR-012/SC-006 cannot close with placeholders) and `grep -n "VALIDATE CONSTRAINT" README.md` shows no advice to run it outside a rolled-back transaction; run `scripts/check.sh` and `scripts/smoke_compose.sh` a final time (the smoke test migrates twice to `0003_contract_constraints` and ingests `matches_valid.csv` in the container); both exit 0

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: T001 → T002 → T003.
- **Foundational (Phase 2)**: after Phase 1. T004, T005, T006, T007 in parallel; T008 last.
- **US1 (Phase 3)**: after Phase 2. Tests: T009, T011–T016 in parallel; T010 before running
  T011–T013 (they import `tests.stages`). Implementation in data-flow order: T017 → T018 → T019 →
  T020 → T021 → T022.
- **US2 (Phase 4)**: after US1 (the seed helper uses `PRE_CONTRACT` and the new `partition`
  signature; the parity test uses `partition_rows`). T023 first; T024, T025, T026 in parallel;
  T027 → T028.
- **US3 (Phase 5)**: after US2 (tests seed, then upgrade to head including `0003`, which is what
  the upsert-safety argument is about). T029 ∥ T030; T031 → T032.
- **US4 (Phase 6)**: T033 → T034 → T035 can start after US1; T036 needs US3 (quickstart §2 is the
  converging re-run).
- **Polish (Phase 7)**: last.

### User Story Dependencies

- **US1 (P1)**: Foundational only. Deliverable alone (MVP): contract enforced by the pipeline.
- **US2 (P2)**: needs US1's `data_contract` (parity is defined against it).
- **US3 (P2)**: needs US1 (same `load.py`/`pipeline.py`) and US2 (upgrade path with constraints).
- **US4 (P3)**: documentation after US1; real-data numbers after US3.

### Within Each User Story

- Test tasks first; confirm they fail for the expected reason; then implement in data-flow order:
  source_contract/errors → data_contract → validate → migration → load → pipeline.
- Each phase ends with `scripts/check.sh` exiting 0.

### Parallel Opportunities

- Phase 2: T004, T005, T006, T007.
- US1 tests: T009, T011, T012, T013, T014, T015, T016.
- US2 tests: T024, T025, T026.
- US3 tests: T029, T030.
- US4: T033 can be written while US2/US3 are in progress.

---

## Parallel Example: User Story 1

```bash
# After T010 (tests/stages.py), write these test files at the same time:
Task: "T011 tests/unit/test_data_contract.py"
Task: "T012 tests/unit/test_validate_contract.py"
Task: "T013 tests/unit/test_accounting.py"
Task: "T014 tests/integration/test_migrations.py + test_constraints.py (0002)"
Task: "T015 tests/integration/test_contract_pipeline.py (US1 part)"
Task: "T016 existing integration tests: snapshot columns, idempotency, cli schema, atomicity meta"
```

## Parallel Example: User Story 2

```bash
# After T023 (seed_pre_contract helper):
Task: "T024 tests/integration/test_constraints.py parity cases"
Task: "T025 tests/integration/test_migrations.py 0003 + upgrade with violators"
Task: "T026 tests/integration/test_contract_pipeline.py SC-001 + upsert safety"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 (pandera) → Phase 2 (test data) → Phase 3 (US1).
2. **Stop and validate**: ingest `matches_contract.csv`; only consistent rows load; report shows
   version 1 and per-rule counts; gate green.

### Incremental Delivery

1. US1 → pipeline contract (MVP).
2. US2 → database enforcement (`0003`), parity proven, safe upgrade.
3. US3 → convergence; upgraded database equals a fresh build.
4. US4 → README and real-data measurement (manual T036).

Each story ends with a green gate and is committed by the orchestrator.

---

## Notes

- Total: 37 tasks (Setup 3, Foundational 5, US1 14, US2 6, US3 4, US4 4, Polish 1).
- The only manual task is T036 (network and stopwatch); everything else is covered by the gate.
- No task modifies `scripts/check.sh`, `.github/workflows/ci.yml`, `Dockerfile` or
  `docker-compose.yml`; pandera reaches the image through `uv sync --frozen --no-dev`.
