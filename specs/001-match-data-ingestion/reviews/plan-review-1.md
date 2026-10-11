# Plan Review — Round 1

**Feature**: `001-match-data-ingestion` | **Reviewer**: plan-reviewer | **Date**: 2026-10-11

Reviewed: constitution v1.1.0, spec.md, plan.md, research.md, data-model.md, contracts/cli.md,
contracts/run-report.schema.json, quickstart.md, tasks.md. No `decisions.md` exists yet.

Verdict: REVISE

Summary: 0 BLOCKER, 3 MAJOR, 10 MINOR. The plan is coherent, traceable and mostly well scoped.
Every FR and SC maps to tasks and named tests. The constitution check holds up when tested
against the details. Three problems would leave a Sonnet implementer stuck or with a gate that
cannot go green: a false claim about polars and ragged rows, an idempotency test that
contradicts the upsert design, and a US1 test that depends on US3 code.

---

## Independent verification of dataset claims

I downloaded the pinned file to a scratch directory outside the repo and profiled it with the
stdlib `csv` module, applying the plan's own rules from R6 and data-model §2.1:

| Claim (research.md R1/R2, quickstart) | Measured | OK |
|---|---|---|
| SHA-256 `ef224cf2…4221`, 45,370,018 bytes, 238,860 lines | identical | ✔ |
| 238,859 records; all non-empty records have 48 fields; 1 empty record at file line 230,559 | identical | ✔ |
| Excluded: `missing_result` 4, `missing_field` 1; `invalid_value`/`same_team`/`invalid_date`/`duplicate` 0 | identical (no value in any column fails the R6/§2.1 parsers) | ✔ |
| Loaded 238,854; 38 leagues; 1,233 raw → 1,221 team keys | identical | ✔ |
| `FTResult` inconsistent with goals | 0 | ✔ |
| E0 2015-07-01…2016-06-30 → 380 rows (quickstart §4) | 380 | ✔ |
| Sample line numbers 64347/73508/192326/112582/174503/230559 | contents match the fixture description | ✔ |
| `King\u0092s Lynn` | the **only** spelling of that club (no `King's Lynn` variant), so SC-006 is not violated | ✔ |
| Odds `0.0` / `HandiSize -99.9` | 8 / 2 occurrences, all pass the `decimal` parser | ✔ |
| Max count value 145 (smallint ample) | 145 | ✔ |

Pinned versions exist (PyPI: `uv==0.12.5`, alembic 1.16.x, psycopg 3.2.x, testcontainers 4.x; Docker
Hub: `python:3.12.11-slim-bookworm`, `postgres:17.6-alpine`). `polars>=1.30,<2` correctly
excludes the released polars 2.0.

I also ran polars 1.44.2 (inside the plan's range) against the reading edge cases. The results
appear in finding 1.

---

## Rulings on points the planner flagged

1. **`invalid_value` and `same_team` reasons beyond the spec — ACCEPT.** FR-008 requires
   home ≠ away to be enforced. Without matching validation rules, one bad row makes a CHECK fail,
   and FR-012 then rolls back the whole run. Silently nulling an unparseable value would break
   Principle I. The spec's reason lists are examples ("e.g.", "the specific reason"), so they are
   not a closed set. Constraint parity is a sound, teachable rule. On the real data both counts
   are 0, so the expected numbers do not move.
2. **No FTResult-vs-score check — ACCEPT.** The spec's Assumptions limit checks to FR-002…FR-006,
   and I measured 0 mismatches. Deferring it to the Phase 2 contracts is right.
3. **No season column; date-range filter — ACCEPT.** Season boundaries really do differ by league
   (R15). A wrong derived season would be silently wrong data. US1-2 is met by
   `test_query_by_league_and_date_range` and quickstart §4 (verified 380).
4. **Extra tooling — ACCEPT all**, with conditions under findings 6 and 9.
   - `verify` is isolated in the Polish phase, justified by SC-004, and teaches reconciliation.
     It is the first thing to cut if time runs short. Its contract must be made consistent
     (finding 6).
   - The advisory lock costs one SQL statement and one test. The "deadlock" rationale is
     overstated: two runs upserting the same rows in the same order mostly block, not deadlock.
     The real benefit is a fast, explicit exit 5 instead of a silent wait, which is enough.
   - pytest-randomly, pytest-socket and jsonschema are dev-only, enforce stated rules, and cost
     one line each.
5. **SC-001/SC-002 measured manually — ACCEPT.** Wall-clock assertions in CI would be flaky and
   machine-dependent. The constitution asks for performance to be *measured and recorded*, which
   T053 does (README "Measured numbers").
6. **Data oddities stored as-is — ACCEPT.** The U+0092 name is valid UTF-8 and is the club's only
   spelling. Repairing it would be name *mapping*, which the spec puts out of scope. `0.0` odds and
   `-99.9` handicap are domain rules for Phase 2. Document all three in the README's
   "known data quirks" (fold this into T052).

---

## Findings

### 1. MAJOR — polars does **not** reject short (ragged) rows; R3/R4 and T019 rely on a false claim

- **Location**: research.md R3 ("errors on ragged lines by default") and R4 step 3; tasks T019
  (`test_read_source_malformed_raises[matches_ragged.csv]`), T030; fixture `matches_ragged.csv`
  ("one row with 47 fields").
- **Problem**: Verified with polars 1.44.2, `pl.read_csv(..., infer_schema=False)`:
  - a row with **fewer** fields is silently padded with nulls (`"1,2"` under header `a,b,c` →
    `('1','2',None)`), and no error is raised;
  - only a row with **more** fields raises `ComputeError`.

  So T019 as written can never pass. Worse, a truncated source line would load with its trailing
  optional columns silently null, which is exactly the silent data loss Principle I forbids.
  Other behaviours check out: a blank line, in the middle or at EOF, is read as one all-null
  record, as R4 says; invalid UTF-8 raises; whitespace is preserved.
- **Required change**: Add an explicit field-count check that runs before the polars body read.
  For example, `extract.check_structure(path)` makes one stdlib-`csv` pass (strict UTF-8). Any
  record whose field count is neither 0 nor `len(header)` raises `MalformedSourceError` naming
  the record number. This takes about a second on 239k rows. Then:
  - correct the R3/R4 text;
  - keep the 47-field fixture and add a 49-field one, both parametrized in
    `test_read_source_malformed_raises`;
  - update T030 to call the new check;
  - state in R4 that polars pads short rows, so a regression in the custom check is caught by
    the 47-field fixture.

### 2. MAJOR — `test_changed_source_updates_match_in_place` contradicts the upsert design

- **Location**: tasks T046; research.md R11 step 5; data-model §5 (matches: "update all non-key
  columns (incl. `source_sha256`, `source_row_number`) only if any differs").
- **Problem**: The modified copy of `matches_valid.csv` has a different SHA-256. Every one of its
  12 rows therefore has a new `source_sha256`, so the `IS DISTINCT FROM` predicate is true for
  all 12 rows. The design yields `matches_updated = 12`, but the test expects `1` and "new
  `source_sha256` on that row" only. The implementer must break either the test or the
  documented semantics, and the tasks forbid editing expectations to make tests pass.
- **Required change**: Pick one semantic and state it in R11, data-model §5 and T046.
  - **Recommended (simplest, honest provenance)**: keep provenance in the comparison. The test
    then asserts `matches_updated == 12`, `matches_inserted == 0`, unchanged `match_id`s, the
    changed goals on the edited row, and the new sha on all rows. Note in R12 that a new source
    version re-stamps every row.
  - **Alternative**: exclude the provenance columns from the `WHERE … IS DISTINCT FROM` and
    still set them only when data changes. This leaves stale shas, which breaks
    `test_db_reconciles_with_run_counts`, quickstart §7 and `verify` (they all link rows by
    `source_sha256`), so it would need more changes.

### 3. MAJOR — a US1 test depends on excluded-row persistence that only arrives in US3

- **Location**: tasks T027 `test_db_reconciles_with_run_counts` (counts `matches` +
  `excluded_rows` per sha = `rows_read`); T036 ("excluded-row persistence is added in T047");
  checkpoint T039 ("`scripts/check.sh` exits 0").
- **Problem**: The test does not name its fixture.
  - With `matches_edge_cases.csv` or `matches_real_sample.csv`, `excluded_rows` is empty until
    T047, so the T039 gate is red.
  - With `matches_valid.csv` the test is vacuous for excluded rows (0 excluded).

  Either way the implementer has to guess, and SC-003's DB-level evidence is weak.
- **Required change**: Move `test_db_reconciles_with_run_counts` to T045
  (`test_excluded_rows.py`, US3), parametrized over `matches_edge_cases.csv` and
  `matches_real_sample.csv`, and update the plan's FR/SC mapping row for SC-003. Also check
  every other T027–T029 test for a hidden dependency on T047, and name the fixture each test
  uses. For example, `test_cli_ingest_report_matches_schema` should use `matches_valid.csv`.

### 4. MINOR — COPY input format is under-specified

- **Location**: T036 (`COPY stg_matches FROM STDIN (FORMAT csv)` from `batch.matches.write_csv()`),
  T047 (excluded rows via `json.dumps` into COPY).
- **Problem**: polars `write_csv()` writes a header row by default. COPY without `HEADER` would
  then try to load the header as data. Hand-serialising JSON into CSV for `stg_excluded` invites
  quoting bugs.
- **Required change**: In T036, specify `write_csv(include_header=False)` with a column order
  identical to the `CREATE TEMP TABLE` list (both generated from `COLUMN_SPECS`). In T047, use
  psycopg's `cursor.copy(...)` + `copy.write_row(...)` with `psycopg.types.json.Jsonb(raw_record)`
  instead of hand-built CSV.

### 5. MINOR — `\d` in regexes matches non-ASCII digits

- **Location**: data-model §2.1, R6, T032 (`^\d+(\.0+)?$`, `^-?\d+(\.\d+)?$`, date/time regexes).
- **Problem**: Rust regex (polars) and Python `re` are Unicode-aware, so `\d` matches e.g.
  `٣` (Arabic-Indic three). Such a value passes the regex, then the cast yields null. Depending
  on implementation, the value is silently nulled or hits a `NOT NULL` violation that aborts the
  run. This breaks constraint parity.
- **Required change**: Use `[0-9]` in every parser regex, and add one parametrized invalid case
  (`"٣"`) to `test_parse_count`.

### 6. MINOR — `verify` contract is inconsistent and its comparison is ambiguous

- **Location**: contracts/cli.md (`verify` output), contracts/run-report.schema.json, research.md
  R24, T050/T051.
- **Problems**:
  - (a) cli.md says stdout always carries "the report", but `verify`'s success shape
    (`checked`, `differences`) is not in the schema.
  - (b) The failure shape adds `differences`, omits `message`, and uses error
    `verification_failed`, which is not in the schema's enum (`additionalProperties: false`).
  - (c) R24 says "compares … home team, away team". Compared literally against the stored display
    name, it reports false differences for `Ajax ` or `Colon Santa FE`. T050 uses `team_key`,
    but T051 only says "per R24".
- **Required change**:
  - State in R24/T051 that teams are compared as `team_key(source) == teams.team_key`, dates as
    ISO strings, and goals as integers.
  - Either add a `verify` branch to the schema (and validate it in T050), or say explicitly
    that the schema covers `ingest` only.
  - Make the verify failure JSON include `message`.

### 7. MINOR — DB-unreachable exit code not wired

- **Location**: contracts/cli.md (exit 7 "DB unreachable"); T037 ("open psycopg connection →
  `load_run`"); T036 maps `psycopg.Error` → `LoadError` only inside `load_run`.
- **Problem**: A `psycopg.OperationalError` from `connect()` in `pipeline.run` falls through to
  `unexpected_error` (exit 1), contradicting the contract.
- **Required change**: T037 wraps connection opening so `psycopg.Error` → `LoadError`. Add a
  `test_cli_exit_codes` case: unreachable `DATABASE_URL` (refused loopback port) → 7.

### 8. MINOR — row-accounting check uses `assert`

- **Location**: T034 ("asserts `accepted.height + excluded.height == rows_read`"), data-model §5.
- **Problem**: `assert` is stripped under `python -O`. The constitution requires failures to
  raise explicitly.
- **Required change**: Raise a named error, e.g. `AccountingError(FootballStatsError)` (exit 1
  or its own code), and test it with a hand-built inconsistent partition.

### 9. MINOR — constitution names the table `rejected_rows`

- **Location**: constitution Principle I ("recorded in a `rejected_rows` table"); plan
  Constitution Check row I; data-model §1.5.
- **Problem**: The plan uses `excluded_rows` (the spec's term) and does not note the deviation,
  although the constitution takes precedence and names the table literally.
- **Required change**: Either rename the table to `rejected_rows`, or add one line to the
  Constitution Check stating that `excluded_rows` fulfils the `rejected_rows` requirement under
  the spec's terminology. Either is fine; just make it explicit.

### 10. MINOR — smoke test can collide with a running dev stack

- **Location**: T040 `scripts/smoke_compose.sh`; T042 `ports: 127.0.0.1:${POSTGRES_PORT:-5432}:5432`.
- **Problem**: The throw-away project still publishes host port 5432. Locally it fails if the dev
  stack or a Homebrew Postgres is running. That is non-deterministic outside CI.
- **Required change**: In the generated temporary `.env`, set `POSTGRES_PORT` to a free port
  (e.g. found with `python3 -c 'import socket; …bind(("127.0.0.1",0))…'`). Alternatively, do not
  publish the port in the smoke run, since the script only uses `exec db psql`.

### 11. MINOR — tests must never write to the default `data/raw`

- **Location**: T026, T027, T029, T050 (settings construction), global test rules.
- **Problem**: `RAW_DATA_DIR` defaults to the relative `data/raw`. A test that builds `Settings`
  with defaults, or a CLI subprocess without the env var, writes read-only (`0o444`) files into
  the repo.
- **Required change**: Add to "Global rules for every task": integration tests always set
  `raw_data_dir`/`RAW_DATA_DIR` to `tmp_path`. Add one shared fixture `settings(db_url, tmp_path)`
  in `tests/integration/conftest.py`.

### 12. MINOR — "pg_dump --schema-only-equivalent catalog snapshot" is undefined

- **Location**: T010 `test_upgrade_is_noop_at_head`, `test_downgrade_then_upgrade_roundtrip`.
- **Problem**: The implementer has to invent the snapshot query, and Sonnet is likely to try to
  shell out to `pg_dump`, which is not installed in the test environment.
- **Required change**: Put `catalog_snapshot(conn)` in `tests/integration/helpers.py`. It returns
  ordered rows from `information_schema.columns` (table, column, data_type, is_nullable),
  `pg_constraint` (conname, contype, `pg_get_constraintdef(oid)`) and `pg_indexes` (indexname,
  indexdef) for schema `public`.

### 13. MINOR — exact `detail` string formats are not pinned

- **Location**: research.md R6 "Detail" column; T009 (`.expected.json` "exact detail string"); T023.
- **Problem**: "Comma-separated missing column names" leaves the separator open: `,` or `, `.
  `MissingColumnsError` uses `, `. The implementer writes both the expected JSON and the code, so
  the tests stay self-consistent, but quickstart/README examples and later phases may disagree.
- **Required change**: Give one literal example per reason in R6. For example, `missing_field` →
  `"HomeTeam, MatchDate"`; `invalid_value` → `"HomeShots"` / `"FTHome, OddHome"`;
  `same_team` → `"ajax"`; `duplicate` → `"duplicate of source row 7"`.

---

## Coverage and consistency notes (no action required)

- **FR-001…FR-013 and SC-001…SC-006** all map to tasks and named tests, or to a documented
  manual measurement (SC-001/002). US1-2, US1-3, US2-1…3 and US3-1…4 are covered. The spec's
  edge cases are each covered by a fixture or test.
- **Constitution**:
  - **Idempotency**: natural-key upserts, plus excluded rows keyed by `(sha, row)`.
  - **Row accounting**: in Python, plus a DB CHECK on `ingest_runs`.
  - **FR-012**: one transaction; `ingest_runs` is inserted last, so failed runs leave no trace.
  - **Migrations-only schema**: Alembic; the app creates only temp tables.
  - **Typed / pure / logging / no bare except**: enforced by mypy strict and ruff rules.
  - **CI order**: lint → types → unit → integration → image build.

  Each of these is concretely satisfied, not just claimed, apart from findings 1, 8 and 9.
- **Simplicity**: Overall weight (54 tasks) is high for Phase 1, but each item traces to an FR, an
  SC, or a constitution rule. No ORM, HTTP, CLI, or config libraries are added, and leagues get no
  lookup table. The `xmax = 0` insert/update counting relies on a Postgres implementation detail.
  It is a common idiom and acceptable, but add a short comment in `load.py` saying so.
- The spec's League entity mentions optional country/name. R26 omits them, which is acceptable
  because the spec marks them optional.
