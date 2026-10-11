# Decisions: Plan Review Responses

**Feature**: `001-match-data-ingestion`

## Round 1 (reviews/plan-review-1.md)

All 13 findings were FIXED. None was rebutted: each one pointed to a real defect or ambiguity that
an implementer would hit. No task was added or removed, so task IDs T001–T054 are unchanged.

The reviewer's caveats on the accepted points have also been applied:
- **Data quirks** (`King\u0092s Lynn`, `0.0` odds, `-99.9` handicap, the blank line) are now
  required content of the README "Known data quirks" section (T052).
- **Advisory-lock rationale** no longer says "deadlock". In research.md R11 and the plan's
  Complexity Tracking, the benefit is now stated as an immediate, explicit exit 5 instead of a
  silent wait on row locks. The lock is transaction-scoped, so it is always released.
- **verify contract** is made consistent (see P1-6).

### P1-1: polars does not reject short (ragged) rows
Finding: polars 1.x pads rows that have too few fields with nulls. Only rows with too many fields
raise. R3/R4 claimed otherwise, so T019 could never pass and a truncated line would load with
silent nulls.

Response: FIXED

Reasoning: The reviewer is right, and this is a Principle I violation, not just a test problem.
- research.md R4 adds step 2: `extract.check_structure(path, expected_fields)`. It is one
  stdlib-`csv` pass with strict UTF-8. Any record whose field count is neither 0 (blank line) nor
  the header width raises `MalformedSourceError` (exit 4), naming the record number and its field
  count.
- The false sentence is removed from R3, and R4 now documents the padding behaviour.
- The single ragged fixture is replaced by two: `matches_ragged_short.csv` (47 fields) and
  `matches_ragged_long.csv` (49 fields). Both are parametrized in
  `test_read_source_malformed_raises`, so removing the custom check makes the gate fail.
- New test: `test_check_structure_accepts_valid_and_blank_lines`.
- T030 and T037 now call the check; the plan's FR-002 and Principle I rows are updated.
- I also rewrote why we fail the whole file rather than excluding the row: with a wrong field
  count, values cannot be mapped to columns reliably, and in a generated CSV this signals
  corruption.

### P1-2: `test_changed_source_updates_match_in_place` contradicts the upsert design
Finding: A modified file has a new SHA-256. Provenance columns are part of the
`IS DISTINCT FROM` predicate, so all 12 rows update, but the test expected 1.

Response: FIXED (the reviewer's recommended option)

Reasoning: Provenance must stay accurate, because `test_db_reconciles_with_run_counts`,
quickstart §7 and `verify` all join on `matches.source_sha256`. Excluding provenance from the
comparison would leave stale hashes and break all three.
- R12 and data-model §5 now state that a new source version re-stamps every match it contains,
  while the same file updates nothing.
- T046 now expects: `matches_inserted` 0, `matches_updated` 12, `matches_unchanged` 0, stable
  `match_id`s, the edited row's new goals, and the new sha on every row.

### P1-3: US1 test depends on excluded-row persistence from US3
Finding: `test_db_reconciles_with_run_counts` in T027 counts `excluded_rows`, which is only
populated by T047, so the US1 gate (T039) would be red or the test vacuous.

Response: FIXED

Reasoning: I moved the test to T045 (`test_excluded_rows.py`, US3), parametrized over
`matches_edge_cases.csv` and `matches_real_sample.csv`, and updated the plan's SC-003 row.

I then checked the other T027–T029 tests for hidden T047 dependencies:
- `test_ingest_report_counts` uses only the report, `matches` and `ingest_runs`.
- The atomicity and CLI tests do not read `excluded_rows`.

Every test in T027 and T029 now names its fixture file. A global rule requires this for all tests.

### P1-4: COPY input format under-specified
Finding: polars `write_csv()` writes a header by default, and hand-serialised JSON in a CSV COPY
invites quoting bugs.

Response: FIXED

Reasoning: T036 now specifies `select(<cols>).write_csv(include_header=False)`. The same
`<cols>` tuple, generated from `COLUMN_SPECS`, is used for the temp table and the COPY column
list. T047 now uses `cursor.copy(...)` with `copy.write_row(...)` and
`psycopg.types.json.Jsonb(raw_record)`.

### P1-5: `\d` matches non-ASCII digits
Finding: In Rust regex and Python `re`, `\d` is Unicode-aware. A value like `٣` passes the
regex and then fails the cast, so it is either silently nulled or causes a NOT NULL abort.

Response: FIXED

Reasoning: All parser regexes in data-model §2.1, R2, R6 and T032 now use `[0-9]`, and §2.1
explains why. Tasks carry a global rule banning `\d` in parsers. New invalid test cases:
`٣` in `test_parse_count` and `٢٠٢١-01-05` in `test_parse_date_invalid`.

### P1-6: verify contract inconsistent; comparison ambiguous
Finding: The verify success shape was not in any schema. The failure shape lacked `message` and
used an error code outside the enum. "Compare home team" was ambiguous: display name or key?

Response: FIXED

Reasoning:
- Added `contracts/verify-report.schema.json` with three branches: success; difference failure
  (`message`, `checked`, `differences` with a fixed `field` enum); generic failure.
- cli.md now references both schemas, shows the full failure JSON including `message`, and
  states the comparison rules.
- R24 and T051 pin the rules: date as an ISO string; teams compared as
  `team_key(source) == teams.team_key`, never display names; goals as integers; a missing DB row
  is reported with field `row`.
- T050 adds `test_verify_cli_report_matches_schema`, which validates both success and failure
  output.
- `run-report.schema.json` is now titled as covering `ingest` plus the generic failure shape.

### P1-7: DB-unreachable exit code not wired
Finding: A connection error from `psycopg.connect` in `pipeline.run` would surface as exit 1,
not the contract's exit 7.

Response: FIXED

Reasoning: T037 now wraps connection opening so that any `psycopg.Error` becomes `LoadError`
(exit 7). `test_cli_exit_codes` gains a case for a refused loopback port → 7, and the plan
mapping has a new "Edge: DB unreachable" row.

### P1-8: Row-accounting check uses `assert`
Finding: `assert` is stripped under `python -O`; the constitution requires explicit failures.

Response: FIXED

Reasoning: Added `AccountingError` (exit 8, `accounting_error`) to errors.py (T013), cli.md
and the run-report schema enum. `Partition.__post_init__` raises it (T034). It is tested by
`test_partition_rejects_inconsistent_counts` (T024), and data-model §5 is updated. I chose a
dedicated exit code over reusing 1 because an invariant breach is a known, named failure, not an
unexpected one.

### P1-9: Constitution names the table `rejected_rows`
Finding: The plan uses `excluded_rows` without noting the deviation.

Response: FIXED (documented, not renamed)

Reasoning: The spec, the user stories and FR-013 all say "excluded". Renaming only the table
would give the code and the docs two words for one concept. The constitution's requirement is
semantic: a table that records the row and its reason. The plan's Constitution Check row I and
data-model §1.5 now state explicitly that `excluded_rows` is the constitution's `rejected_rows`
table under the spec's terminology.

### P1-10: Smoke test can collide with a running dev stack
Finding: The smoke project still publishes host port 5432.

Response: FIXED

Reasoning: T040 now sets `POSTGRES_PORT` in the generated temporary `.env` to a free port,
found by binding `127.0.0.1:0` with a short `python3` command. I kept port publishing, rather
than removing it for smoke runs only, so the smoke test uses the same compose file as
development.

### P1-11: Tests must never write to the default `data/raw`
Finding: The default relative `RAW_DATA_DIR` lets tests write read-only files into the repo.

Response: FIXED

Reasoning: T012 adds a shared `settings(db_url, tmp_path)` fixture with `raw_data_dir` set to
`tmp_path / "raw"`. A new global rule in tasks.md requires that fixture for integration tests and
an explicit `RAW_DATA_DIR=tmp_path` for every CLI subprocess (T029, T050).

### P1-12: "pg_dump-equivalent catalog snapshot" undefined
Finding: The implementer might shell out to `pg_dump`, which is not installed in the test
environment.

Response: FIXED

Reasoning: T012 defines `helpers.catalog_snapshot(conn)` as pure SQL. It returns ordered rows
from `information_schema.columns`, from `pg_constraint` joined to `pg_class` (with
`pg_get_constraintdef`), and from `pg_indexes`, all for schema `public`. T010 uses it in both
migration tests.

### P1-13: Exact `detail` string formats not pinned
Finding: The separator and literal format of `detail` were open.

Response: FIXED

Reasoning: The R6 table now gives the format plus one literal example per reason:
- `missing_field`: column names joined by `", "` in contract order, e.g.
  `Division, MatchDate, HomeTeam, AwayTeam`.
- `invalid_date`: the raw value, e.g. `2021-02-30`.
- `missing_result`: e.g. `FTHome, FTAway, FTResult`.
- `invalid_value`: e.g. `HomeShots` or `FTHome, OddHome`.
- `same_team`: the team key, e.g. `ajax`.
- `duplicate`: e.g. `duplicate of source row 7`.

This matches the `", "` join already used by `MissingColumnsError`.

### Note (no action required): `xmax = 0`
The reviewer asked for a code comment about relying on a Postgres implementation detail. T036
now requires that comment, and it names the two tests that guard the behaviour
(`test_second_run_reports_all_unchanged`, `test_changed_source_updates_match_in_place`).
