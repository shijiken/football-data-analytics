# Plan Review — Round 2

**Feature**: `001-match-data-ingestion` | **Reviewer**: plan-reviewer | **Date**: 2026-10-11

Verdict: APPROVE

**Counts**: 0 BLOCKER, 0 MAJOR, 1 MINOR.

The planner fixed all 13 round-1 findings and rebutted none (decisions.md P1-1…P1-13). I checked
each fix in the changed files themselves, not just in the summary. I then looked for new
inconsistencies across `research.md`, `data-model.md`, `plan.md`, `tasks.md`, `contracts/cli.md`,
`contracts/run-report.schema.json` and the new `contracts/verify-report.schema.json`. No settled
point is re-raised.

## Verification of round-1 fixes

| # | Finding | Where verified | Status |
|---|---|---|---|
| P1-1 (MAJOR) | polars pads short rows | R4 step 2 adds `check_structure`; R3 no longer makes the false claim; T030 defines `check_structure`; T037 calls it after `check_header` and before `read_source`; fixtures `matches_ragged_short.csv`/`_long.csv`; cli.md exit 4 wording; plan Principle I row | Fixed, with one test-wiring gap (finding 1 below) |
| P1-2 (MAJOR) | changed-source test vs upsert | R12 now has a "re-stamps provenance" paragraph; data-model §5; T046 expects inserted 0 / updated 12 / unchanged 0, stable ids, new sha on all rows. Quickstart §8 (same file → 0 updated) is still consistent | Fixed |
| P1-3 (MAJOR) | US1 test depends on US3 | `test_db_reconciles_with_run_counts` moved to T045, parametrized over the edge-case and real-sample fixtures; plan SC-003 row updated; every T027/T029 test names its fixture; `test_ingest_report_counts` explicitly avoids `excluded_rows` | Fixed |
| P1-4 | COPY format | T036 uses `select(<cols>).write_csv(include_header=False)` with one shared column tuple; T047 uses `copy.write_row` + `Jsonb` | Fixed |
| P1-5 | `\d` Unicode digits | `[0-9]` in R2/R6/§2.1/T032; global rule in tasks; `٣` and `٢٠٢١-01-05` cases in T021 | Fixed |
| P1-6 | verify contract | `verify-report.schema.json` has success, difference-failure (with `message`) and generic-failure branches; R24/T051 pin key-based team comparison and the `row` field; T050 adds a schema test for both outcomes | Fixed |
| P1-7 | DB unreachable → 7 | T037 wraps `psycopg.connect`; T029 adds a refused-port → 7 case; plan has an "Edge: DB unreachable" row | Fixed |
| P1-8 | `assert` | `AccountingError` (exit 8) in T013, cli.md, the run-report enum and data-model §5; `Partition.__post_init__` raises it; `test_partition_rejects_inconsistent_counts` | Fixed |
| P1-9 | `rejected_rows` naming | plan Constitution Check row I and data-model §1.5 state the equivalence | Fixed |
| P1-10 | smoke port collision | T040 writes a free `POSTGRES_PORT` into the temporary `.env` | Fixed |
| P1-11 | tests writing to `data/raw` | shared `settings` fixture (`tmp_path / "raw"`) in T012; global rule; explicit `RAW_DATA_DIR` in the T029/T050 subprocess tests | Fixed |
| P1-12 | catalog snapshot | `helpers.catalog_snapshot` defined as pure SQL in T012 and used in T010 | Fixed |
| P1-13 | detail strings | R6 gives the `", "` format plus a literal example per reason | Fixed |

The caveats from the round-1 rulings were also applied:
- The README "Known data quirks" section is required in T052, and its counts (8 / 2 / line
  230,559) match my round-1 measurements.
- The advisory-lock rationale no longer mentions deadlocks.

I found no stale references. `matches_ragged.csv` is gone from every artifact. The error enums in
cli.md, both schemas and T013 agree. The new exit code 8 does not clash with any existing code.

## Findings

### 1. MINOR — `test_read_source_malformed_raises` calls the wrong function for the short-row case

- **Location**: tasks T019 (parametrizes `matches_ragged_short.csv`, `matches_ragged_long.csv` and
  `matches_invalid_utf8.csv` under `test_read_source_malformed_raises`); T030 (`check_structure`
  and `read_source` are separate functions, and `read_source` is plain `pl.read_csv`); research.md
  R4 (last paragraph).
- **Problem**: As named, the test exercises `read_source`. On the 47-field file, `read_source`
  silently pads the row (the very behaviour P1-1 is about), so that case fails. Only the pipeline
  calls `check_structure`. The implementer must guess between two fixes:
  - change the test to call `check_structure`;
  - have `read_source` call `check_structure` internally, which means two passes if the pipeline
    also calls it.
- **Required change** (non-blocking; fold in at implementation time): in T019, rename the test to
  `test_malformed_source_raises` and make it call `check_structure(path, 48)` for all three
  fixtures (stdlib `csv` also raises on invalid UTF-8). Keep the polars `ComputeError`/UTF-8
  mapping in `read_source` as defence in depth, covered by one `read_source` case on
  `matches_ragged_long.csv`. Update the R4 sentence to match.

## Notes (no action required)

- `verify-report.schema.json` deliberately omits `malformed_source`/`accounting_error` from its
  generic-failure enum. That is acceptable: `verify` reads the content-addressed, read-only file
  that a successful ingest already validated.
- The plan is still heavy for Phase 1 (54 tasks), but every item traces to an FR, an SC, a
  constitution rule, or a round-1 finding. The round-2 changes added no new tools or layers.
