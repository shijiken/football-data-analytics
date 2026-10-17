# Code Review — Round 2

**Feature**: `001-match-data-ingestion` | **Reviewer**: code-reviewer | **Date**: 2026-10-11
**Diff reviewed**: `7623d38..3ce3396` (fixes for code-review-1, decisions.md C1-1 … C1-6)

Verdict: APPROVE

**Counts**: 0 BLOCKER, 0 MAJOR, 2 MINOR.

## Gate (run independently)

- `scripts/check.sh` **exit 0**. Ruff format, ruff check and mypy `--strict` all passed. 122 unit
  tests passed, which is 2 more than round 1. 64 integration tests passed, also 2 more than
  round 1.
- The diff adds no `noqa`, `type: ignore`, `skip` or `xfail`.
- The migration change is whitespace only. `test_upgrade_creates_schema`,
  `test_upgrade_is_noop_at_head` and `test_downgrade_then_upgrade_roundtrip` still pass. The SQL
  inside the string is unchanged apart from indentation, which I checked line by line.

## Verification of round-1 fixes (read in the diff, not the summary)

| # | Fix | Verified | Status |
|---|---|---|---|
| C1-1 | `add_team_keys` maps a normalized-empty key or display name to null, then nulls `home_team`/`away_team` for those rows | `validate._names_where` checks `pl.col("home_team").is_null() & ~invalid`, so the existing `missing_field` rule now fires. `raw_record` is built in `parse_frame` before this step, so the original bytes are kept. New cases `"\x1c"` and `" \x1f\x1e "` exist and expect `missing_field` with detail `HomeTeam`/`AwayTeam`. | Fixed |
| C1-2 | count of `stg_matches ⋈ teams ⋈ teams` must equal `batch.matches.height`, otherwise `AccountingError` | It sits inside `conn.transaction()` and before the upsert. `AccountingError` is not a `psycopg.Error`, so it surfaces as exit 8 and is not rewrapped. I exercised the path myself (see (a)). | Fixed, but untested: finding 1 |
| C1-3 | `verify` counts records in one pass and materializes only the chosen rows in a second | `_count_records` and `_read_records` are correct: same 1-based numbering, and an empty line still counts as a record. `FileNotFoundError` is still mapped to exit 3. | Fixed: measured below |
| C1-4 | plan.md (Principle I and FR-002 rows), research.md R4, tasks.md T046 | All four now name `test_malformed_source_raises` / `test_read_source_malformed_raises` correctly, and T046 describes the real assertion | Fixed |
| C1-5 | `mkdir` moved inside `try`; `OSError` caught; a `Content-Length` that is unparsable or does not match is treated as a short read | The `SourceUnavailableError` for a short read is not an `OSError`, so it is not rewrapped. `finally` cleanup can no longer mask the original error. New tests `test_unparsable_content_length_is_source_unavailable` and `test_unwritable_raw_dir_is_source_unavailable` pass. | Fixed |
| C1-6 | Compose `SOURCE_URL: ${SOURCE_URL:-}`; assertion rewritten; migration indentation | All three done. `load_settings` maps `""` to `PINNED_SOURCE_URL` via `.strip() or …`. | Fixed (finding 2 is about a test) |

### (a) The C1-2 `AccountingError` path should be tested

decisions.md C1-2 says the path "cannot be reached through the pipeline, so it has no dedicated
test". That is true for `pipeline.run`. It is not true for `load_run`, which is exactly how
`test_db_failure_mid_load_rolls_back` already injects a broken batch. I checked this with a
scratch script outside the repo:
- It ran against testcontainers `postgres:17.6-alpine` after `alembic upgrade head`.
- It called `load.load_run` on the `matches_valid.csv` batch with the team `derby` removed from
  `batch.teams`.

The result was `AccountingError: 12 staged matches but 8 resolve …`, and every table count was
afterwards 0, so the rollback works. The test is about 15 lines. Without it, a later edit could
silently break this guard, which is the only thing standing between an INNER JOIN drop and a
falsely balanced `ingest_runs` row (Principle I). See finding 1.

### (b) C1-3 memory, re-measured

I used the same method as round 1. The synthetic file had 238,859 rows × 48 columns (47.6 MB,
built from `matches_valid.csv`). I ran `_count_records` followed by `_read_records` and read peak
RSS from `getrusage`:

| Mode | Round 1 (`_read_source`) | Round 2 |
|---|---|---|
| `--sample 20` (default, quickstart §9) | 862 MB | **69 MB** (interpreter baseline 59 MB) |
| `--sample 0` (all rows) | 862 MB | 690 MB |

The default path is fixed, at about 12× lower peak memory. Checking every row still needs about
690 MB. That is inherent in materializing every record, and it is opt-in. Streaming the
comparison would remove it, but it is not required for this phase.

## Regression check

- The C1-1 nulling runs after the key mapping and only on rows whose key is null. Valid names
  are untouched. The idempotency, team-variant and verify oracle tests still pass on all three
  fixtures.
- The C1-5 `except OSError` also covers `TimeoutError` and `URLError`, so the tuple is partly
  redundant but correct. The interrupted-download test still sees `IncompleteRead`, which is not
  an `OSError` and stays listed separately.
- The C1-2 guard adds one aggregate over the staging table per run, which is negligible against
  the upsert.
- The import reorder in `test_extract_download.py` and `test_validate_rows.py` is a ruff
  isort-classification change only.

## Findings

### 1. MINOR — The new `AccountingError` guard in `load_run` has no test

- **Location**: `src/football_stats/load.py:186-195`; `tests/integration/test_atomicity.py`.
- **Problem**: The guard is untested defence for Principle I. The claim that it cannot be
  reached is about `pipeline.run`, not `load_run`, and I reached it directly (see (a)). The error
  message also says "resolve to a league and two teams", but the query joins only `teams`.
- **Required change**:
  - Add `test_unresolvable_team_raises_accounting_error_and_rolls_back` to `test_atomicity.py`.
    Reuse the `_broken_batch` pattern: drop one team from `batch.teams`, assert `AccountingError`,
    and assert that `helpers.snapshot(db_conn)` is unchanged. Parametrize it over empty and
    preloaded databases like the neighbouring test.
  - Reword the message to "resolve to two known teams".

### 2. MINOR — Compose now passes `SOURCE_URL=""`, which no test covers

- **Location**: `docker-compose.yml:32`; `tests/unit/test_config.py:25-27`.
- **Problem**: `test_config_defaults` covers an absent `SOURCE_URL` only. The container now
  always receives the variable set to an empty string. That path works, because `.strip() or`
  maps it to the pinned URL, but it is not pinned by a test. A refactor to
  `env.get("SOURCE_URL", PINNED_SOURCE_URL)` would break every Compose ingest while the gate
  stayed green.
- **Required change**: in `test_config_defaults`, also assert
  `load_settings({"DATABASE_URL": …, "SOURCE_URL": ""}).source_url == PINNED_SOURCE_URL`, and
  the same for `"   "`.

## Notes (no action required)

- T053 is still the only open task. The README `TODO(T053)` placeholders are expected.
- When T053 runs quickstart §9, `verify --sample 20` now needs well under 100 MB for the source
  side. The DB-side `db_rows` dict, with one small tuple per match, is not covered by my
  measurement because the synthetic file loads only 12 distinct matches. I expect it to be in
  the tens of MB.
