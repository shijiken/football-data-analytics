# Code Review — Round 1

**Feature**: `001-match-data-ingestion` | **Reviewer**: code-reviewer | **Date**: 2026-10-11
**Diff reviewed**: `544b87a..1c3f1f6` (6 implementation commits, T001–T052 and T054)

Verdict: APPROVE

**Counts**: 0 BLOCKER, 0 MAJOR, 6 MINOR.

As instructed, the `TODO(T053)` placeholders in the README are not findings. T053 is the only
unticked task.

## Gate (run independently)

- `scripts/check.sh` **exit 0**. The steps were ruff format, ruff check, mypy `--strict` on
  `src tests`, then 120 unit tests (sockets disabled, seed 20261011) and 62 integration tests
  against testcontainers `postgres:17.6-alpine`. The only warning is a testcontainers
  deprecation of the `testcontainers.postgres` import path.
- `scripts/smoke_compose.sh` **exit 0**. It migrated twice with an identical `\dt` and
  `alembic_version`, then ingested `matches_valid.csv` in the container: 12/12/0, inserted 12.
- Gate integrity: no `noqa`, `type: ignore`, `skip` or `xfail` anywhere in `src`, `tests`,
  `migrations` or `scripts`. `check.sh` runs under `set -euo pipefail`. Unit tests run with
  `--disable-socket`. Every integration test gets its own database cloned from a migrated
  template. No timestamps are asserted. No `.env` or data files are tracked. `print` appears only
  in the smoke script's inline `python3 -c`, which is not package code.
- Every test named in plan.md, tasks.md and data-model.md exists.

## FR / SC verification (assertion strength checked by reading each test)

| Req | Proving test(s) | Strong enough? |
|---|---|---|
| FR-001 | `test_download_keeps_unmodified_copy` (bytes, sha, mode 0444), `test_download_http_loopback_streams_and_hashes` (multi-chunk), `test_existing_raw_copy_reused`, `test_run_records_source_and_raw_path` | Yes |
| FR-002 | `test_check_header_*`; `test_malformed_source_raises` (47/49 fields, UTF-8, with the message); `test_missing_columns_stops_before_db` (exact missing tuple, DB snapshot unchanged); CLI exit 4 for both error codes | Yes |
| FR-003 | parser tests (incl. `2021-02-30`, `٢٠٢١-01-05`); `test_invalid_date_excluded` checks reason and detail | Yes |
| FR-004 / SC-006 | `test_team_key_normalizes_formatting` (NBSP, em space, combining marks, ß); `test_team_spelling_variants_single_team` (DB-level, canonical name) | Yes; see finding 1 for one gap |
| FR-005 | `test_missing_required_field_excluded` (×7 incl. blank line and multi-field detail); `test_missing_result_excluded`; `test_optional_nulls_stored_as_null` (DB NULLs and full-row typed values) | Yes |
| FR-006 | `test_duplicate_keeps_first_excludes_rest`, `test_duplicate_detected_after_normalization`, `test_invalid_row_never_wins_duplicate`, `test_duplicate_detail_points_to_kept_row` | Yes |
| FR-007 / FR-008 / FR-009 | `test_upgrade_creates_schema` (exact FK set), constraint tests, `test_upgrade_is_noop_at_head`, `test_downgrade_then_upgrade_roundtrip`, `test_source_contract_matches_db_columns` (set equality incl. types) | Yes |
| FR-010 / SC-003 | `test_partition_rejects_inconsistent_counts`; `test_ingest_report_counts` (per-reason counts derived from the sidecar JSON, report and `ingest_runs`); `test_db_reconciles_with_run_counts`; the DB CHECK test | Yes |
| FR-011 / SC-005 | `test_second_run_leaves_identical_state` ×3 fixtures, `test_second_run_reports_all_unchanged`, `test_changed_source_updates_match_in_place` | Yes (ruling below) |
| FR-012 | `test_db_failure_mid_load_rolls_back` (empty and preloaded; the failing batch also adds a new league and team, both of which must roll back), `test_concurrent_run_rejected`, `test_interrupted_download_leaves_no_file_and_db_unchanged` | Yes |
| FR-013 | `test_each_reason_persisted_with_raw_record` (raw_record equals an independent stdlib-csv re-read; the blank line gives 48 null keys), `test_excluded_rows_link_to_run` | Yes |
| SC-004 | `test_every_loaded_match_equals_source` (independent oracle, all rows), tamper/seed/schema tests | Yes |
| SC-001 / SC-002 | Manual (T053), outside this round | — |

## Rulings on implementer deviations (decisions.md I-1 … I-6)

- **I-1 (malformed-source test calls `check_structure`)**: **ACCEPT**. This is exactly what
  plan-review-2 finding 1 recommended. `test_read_source_malformed_raises` keeps the
  defence-in-depth case. The documentation was not updated to match: see finding 4.
- **I-2 (team columns of the source contract)**: **ACCEPT**. A name column that becomes a
  foreign key cannot exist in `matches`. The test I read is stronger than the task wording: it
  asserts set equality of every `matches` column and its type, including `home_team_id`,
  `away_team_id`, provenance and `match_id`. A drifted or extra column would fail it.
- **I-3 (`pythonpath = ["src"]`, explicit `PYTHONPATH` for CLI subprocesses)**: **ACCEPT**. It is
  harmless on Linux. The installed-package path is still exercised by the Docker image in the
  smoke test, so an import or packaging bug cannot hide behind the source-tree path.
- **I-4 (commit trailer)**: **ACCEPT**. This is attribution, not code, and it has no effect on
  behaviour or on the gate.
- **I-5 (smoke test mounts a `mktemp -d` copy of the fixture)**: **ACCEPT**. It is portable,
  is cleaned up in the `EXIT` trap, and I verified that it passes. The macOS permission-prompt
  caveat for quickstart §10 is documented.
- **I-6 (T053 left for the orchestrator)**: **ACCEPT**. The README placeholders are clearly
  marked. Finding 3 should be fixed before T053 runs quickstart §9 on the real file.

### Ruling on the change to `test_second_run_leaves_identical_state`: **ACCEPT. It is still a strong FR-011 / SC-005 test.**

T046 asked for "`ingest_runs` has 2 rows with identical count columns". Read literally, that
cannot hold: run 1 has `matches_inserted = N` and run 2 must have `matches_inserted = 0`. The
implementer's version compares the only columns that should be identical: `source_url`,
`source_sha256`, `source_bytes`, `raw_path`, `rows_read`, `rows_loaded`, `rows_excluded` and
`excluded_by_reason`. It then pins run 2's split to exactly `(0, 0, rows_loaded)`. I checked
that the test fails for each realistic regression:

| Regression | Caught by |
|---|---|
| Upsert inserts duplicates / natural key ignored | full-row snapshot of `matches` (also `excluded_rows`) |
| Surrogate ids churn (delete-and-reinsert, truncate-reload) | snapshot includes `match_id`, `team_id`, `excluded_row_id` |
| `IS DISTINCT FROM` guard removed (no-op rewrites) | `run_two[9:] == (0, 0, N)`: `matches_updated` would be N |
| `excluded_rows.run_id` overwritten on re-run | `excluded_rows` snapshot includes `run_id` |
| Team display name flips between runs | `teams` snapshot |
| Run 2 silently failed / rolled back | `len(ingest_runs) == 2` |
| Run 2 mutates run 1's audit row | `second["ingest_runs"][0] == first["ingest_runs"][0]` |
| Different read/exclusion accounting on re-run | `run_one[1:9] == run_two[1:9]` |

The test is parametrized over the valid, edge-case and real-sample fixtures, so it covers
excluded rows, duplicates and the real data's formatting. `ingest_runs` growing by one row per
run is the designed audit log (data-model §1.4), not a duplicate under SC-005. Please also fix
the T046 wording in tasks.md so that it describes what the test asserts (folded into finding 4).

## Findings

### 1. MINOR — Constraint-parity gap: a team name made only of C0 separator characters gets an empty `team_key` and aborts the whole run

- **Location**: `src/football_stats/clean.py:21-24` (blankness is decided by polars
  `strip_chars`), `clean.py:112-114` (the key comes from Python `str.split`), and
  `src/football_stats/validate.py:91-96` (`missing_field` is decided before keys exist).
- **Problem**: Python's `str.split()` treats `\x1c`–`\x1f` as whitespace, but Rust/polars
  `strip_chars()` does not. A `HomeTeam` of `"\x1c"` is therefore non-null after parsing and
  passes the `missing_field` rule. `team_key` then turns it into `""`. I probed this:
  `partition_rows([{"HomeTeam": "\x1c"}])` gives **accepted** with `home_team_key == ''`. The
  load then hits `teams.team_key <> ''` (and `btrim(name) <> ''`). The whole run fails with exit 7
  instead of excluding one row. The failure is loud and fully rolled back, so it is not silent
  loss. It does break data-model §3's promise that "a data problem becomes an excluded row, never
  a failed run". It cannot happen in the pinned file, whose only control character is U+0092 in
  `King\u0092s Lynn`.
- **Required change**: decide team blankness on the normalized key. For example, in
  `add_team_keys` map a name whose `team_key` is `""` to a null key and a null display name. Then
  make the `missing_field` check for `HomeTeam`/`AwayTeam` test the key, not the stripped name.
  Add `{"HomeTeam": "\x1c"}` → `missing_field`/`HomeTeam` to
  `test_missing_required_field_excluded`.

### 2. MINOR — `matches_unchanged` is derived by subtraction, so a row dropped by the INNER JOIN would be reported as "unchanged"

- **Location**: `src/football_stats/load.py:99-103` (`JOIN teams h … JOIN teams a …`) and
  `load.py:190-193` (`unchanged = batch.matches.height - len(returned)`).
- **Problem**: If a staged match ever had no matching `teams` row, the INNER JOIN would drop it
  silently. It would then be counted as unchanged, and both `ingest_runs` CHECKs would still
  balance. That is a silent-loss path that the accounting is supposed to make impossible
  (Principle I). It cannot happen today, because `teams` and `matches` come from the same
  accepted frame. However, the guarantee rests on construction alone, with no check behind it.
- **Required change**: before the upsert, count
  `stg_matches s JOIN teams h … JOIN teams a …`. If the count differs from
  `batch.matches.height`, raise `AccountingError`. Alternatively, derive `unchanged` from an
  explicit count of conflicting keys. Either option costs one statement.

### 3. MINOR — `verify` materializes every source record as a dict (~860 MB peak) even for a 20-row sample

- **Location**: `src/football_stats/verify.py:43-48` (`_read_source`) and `verify.py:118-130`.
- **Problem**: I ran `_read_source` on a synthetic file with 238,859 rows and 48 columns
  (47.6 MB, built from `matches_valid.csv`). Peak RSS was **862 MB**. Quickstart §9 runs `verify`
  inside the ingest container on the real file. This should still fit Docker Desktop's default
  memory, but it is about 20× more than needed and could be OOM-killed on a constrained runner.
- **Required change**: keep the records as `list[list[str]]` together with the header index,
  then build dicts, or index by column, only for the chosen row numbers. Alternatively, count the
  records in a first pass and materialize only the sampled rows in a second. Behaviour and tests
  stay the same.

### 4. MINOR — Spec artifacts not updated for I-1 and the T046 assertion

- **Location**: `plan.md:57` and `plan.md:120` (the FR-002 row lists
  `test_read_source_malformed_raises` with "47- and 49-field rows, invalid UTF-8"),
  `research.md:127` (R4 still says that test is parametrized with the 47-field row), and
  `tasks.md` T046 ("identical count columns").
- **Problem**: plan-review-2 finding 1 asked for the R4 sentence to be updated alongside the
  rename. The FR→test mapping now points at a test that no longer proves the short-row case.
- **Required change**: point the FR-002 and Principle I rows and R4 at
  `test_malformed_source_raises`. Describe `test_read_source_malformed_raises` as the 49-field
  defence-in-depth case. Reword T046 to "identical source and read/loaded/excluded columns; run 2
  inserted = updated = 0, unchanged = rows_loaded".

### 5. MINOR — Two `fetch_to_raw` failures escape as `unexpected_error` (exit 1) instead of `source_unavailable` (exit 3)

- **Location**: `src/football_stats/extract.py:132` and `extract.py:145-152`.
- **Problem**: A non-numeric `Content-Length` raises `ValueError` from `int(expected)`. A
  disk-full or permission `OSError` while writing `.partial-<pid>` is also not in the caught
  tuple. Both are "could not be copied completely" failures under cli.md exit 3. The file is
  still cleaned up by `finally` and the DB is untouched, so only the exit code and message are
  wrong.
- **Required change**: catch `OSError` (it covers `FileNotFoundError` and `ConnectionError`,
  which are already listed) and treat an unparsable `Content-Length` as a short read. One
  loopback test with `Content-Length: abc` is enough.

### 6. MINOR — Small simplicity and readability nits

- `docker-compose.yml:32` repeats the full pinned URL as the `SOURCE_URL` default, so the URL now
  has two sources of truth alongside `source_contract.PINNED_SOURCE_URL`. `load_settings` already
  maps an empty `SOURCE_URL` to the pinned default. Use `SOURCE_URL: ${SOURCE_URL:-}`.
- `tests/integration/test_extract_download.py:75`:
  `assert not list(...) if (...).exists() else True` is hard to read. Use
  `assert not (tmp_path / "raw").exists() or not any((tmp_path / "raw").iterdir())`, which is
  the same form as line 93.
- `migrations/versions/0001_initial_schema.py:46-85`: the optional `matches` columns are
  indented differently from the key columns above them. This is cosmetic only, but this file is
  the schema's source of truth.

## Notes (no action required)

- I checked these correctness points and found no defect:
  - The advisory lock is transaction-scoped and is checked before any write.
  - `ConcurrentRunError` raised inside `conn.transaction()` rolls back and is not re-wrapped as
    `LoadError`.
  - COPY CSV writes nulls as unquoted empty fields, so NULL round-trips. Floats round-trip
    exactly: the real-sample idempotency case would show updates otherwise.
  - Regexes use `[0-9]` everywhere.
  - The year-0 date guard is correct (PostgreSQL rejects year 0).
  - All SQL is built with `psycopg.sql.Identifier` or bound parameters. The only f-string SQL is
    in tests, over constant table names.
  - `check_structure` and `read_source` agree on record counts for mid-file, trailing,
    double-trailing and CRLF blank lines (probed).
- On a synthetic 239k-row file, the pure stages take 0.4 s for the structure check and 1.5 s to
  parse and partition. Nothing here threatens SC-002. T053 will provide the measured number.
- The README's example report and "expected" counts are labelled as expectations pending T053.
  That is fine as long as T053 replaces them with measured values.
