# Contract: `football-stats` CLI and structure command

**Feature**: `001-match-data-ingestion` | Decisions: research.md R17, R18, R24

Entry point: `[project.scripts] football-stats = "football_stats.cli:main"`.
Standard streams: **stdout** carries exactly one JSON document (the report) on success *and* on
handled failure; **stderr** carries log lines. Nothing else is written to stdout.

## Environment

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `DATABASE_URL` | yes | — | `postgresql://user:pass@host:port/db` |
| `SOURCE_URL` | no | commit-pinned raw URL (research.md R1) | where `ingest` downloads from |
| `RAW_DATA_DIR` | no | `data/raw` | directory for content-addressed raw copies |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |

A missing `DATABASE_URL` or invalid `LOG_LEVEL` → exit 2 with a message naming the variable.

## `football-stats ingest [--source-file PATH]`

Downloads `SOURCE_URL` (or copies `PATH` when `--source-file` is given), keeps the raw copy, checks
the header, cleans, validates, and loads in one transaction.

Success report (exit 0) — schema: [run-report.schema.json](./run-report.schema.json):

```json
{
  "status": "succeeded",
  "run_id": 1,
  "source_url": "https://raw.githubusercontent.com/.../data/Matches.csv",
  "source_sha256": "ef224cf2...",
  "raw_path": "data/raw/matches-ef224cf2....csv",
  "rows_read": 238859,
  "rows_loaded": 238854,
  "rows_excluded": 5,
  "excluded_by_reason": {"missing_field": 1, "invalid_date": 0, "missing_result": 4,
                         "invalid_value": 0, "same_team": 0, "duplicate": 0},
  "matches_inserted": 238854,
  "matches_updated": 0,
  "matches_unchanged": 0,
  "duration_seconds": 41.2
}
```

Invariants (tested): `rows_read == rows_loaded + rows_excluded`;
`rows_excluded == sum(excluded_by_reason.values())`; all six reason keys are always present;
`rows_loaded == matches_inserted + matches_updated + matches_unchanged`.

Failure report (exit ≠ 0):

```json
{"status": "failed", "error": "missing_columns", "message": "Source file is missing expected columns: AwayTeam, FTHome"}
```

## `football-stats verify [--sample N] [--seed S] [--source-file PATH]`

Compares N (default 20) seeded-random accepted source rows (default seed 42) with the database
(date, home team, away team, FT score), using the raw file of the latest run unless `PATH` is given.
`--sample 0` means all rows.

```json
{"status": "succeeded", "checked": 20, "differences": []}
```

On differences: `"status": "failed", "error": "verification_failed"`, `differences` lists
`{"source_row_number": n, "field": "...", "source": "...", "database": "..."}`; exit 6.

## Exit codes

| Code | `error` | When | DB changed? |
|---|---|---|---|
| 0 | — | success | yes (ingest) / no (verify) |
| 1 | `unexpected_error` | any unhandled exception (logged with traceback) | no (rolled back) |
| 2 | `configuration_error` | bad/missing env var or CLI usage (argparse also uses 2) | no |
| 3 | `source_unavailable` | download failed, timed out, short read, file not found | no |
| 4 | `missing_columns` / `malformed_source` | FR-002 header check failed, or file not parseable as CSV/UTF-8 | no |
| 5 | `concurrent_run` | another ingest holds the advisory lock | no |
| 6 | `verification_failed` | `verify` found differences | no |
| 7 | `database_error` | DB unreachable or a statement failed during load | no (rolled back) |

## Structure command (FR-009)

`alembic upgrade head` (reads `DATABASE_URL`). Applying it to a database already at head is a
no-op with exit 0 (US2-3). In Compose: the `migrate` service runs it.

## Compose commands

| Command | Effect |
|---|---|
| `docker compose up -d` | start `db`, run `migrate` once (US2-1, US2-2) |
| `docker compose run --rm ingest` | start `db`/`migrate` if needed, then run `football-stats ingest` |
| `docker compose run --rm ingest football-stats verify` | SC-004 spot check |
| `docker compose down -v` | stop and delete the database volume |
