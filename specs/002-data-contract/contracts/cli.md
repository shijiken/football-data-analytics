# Contract: CLI changes for the data contract

**Feature**: `002-data-contract` | Base contract:
[001 contracts/cli.md](../../001-match-data-ingestion/contracts/cli.md) (everything not listed here
is unchanged: commands, flags, environment variables, verify report, Compose commands).

## `football-stats ingest [--source-file PATH]`

No new flags or environment variables. The run date used by `date_in_range` is the UTC date at which
the run starts (not configurable).

Success report (exit 0) — schema: [run-report.schema.json](./run-report.schema.json), which
supersedes 001's for `ingest`:

```json
{
  "status": "succeeded",
  "run_id": 2,
  "source_url": "https://raw.githubusercontent.com/.../data/Matches.csv",
  "source_sha256": "ef224cf2...",
  "raw_path": "data/raw/matches-ef224cf2....csv",
  "contract_version": 1,
  "rows_read": 238859,
  "rows_loaded": 237473,
  "rows_excluded": 1386,
  "excluded_by_reason": {"missing_field": 1, "invalid_date": 0, "missing_result": 4,
                         "invalid_value": 0, "same_team": 0, "contract_violation": 1381,
                         "duplicate": 0},
  "violations_by_rule": {"ht_le_ft_home": 0, "ht_le_ft_away": 0, "ft_result_consistent": 0,
                         "ht_result_consistent": 0, "sot_le_shots_home": 7,
                         "sot_le_shots_away": 13, "odds_min": 8, "max_odds_ge_avg": 1357,
                         "date_in_range": 0},
  "matches_inserted": 0,
  "matches_updated": 0,
  "matches_unchanged": 237473,
  "matches_removed": 1381,
  "excluded_rows_removed": 0,
  "duration_seconds": 20.1
}
```

(`duration_seconds` above is illustrative, not measured.)

(Values shown: the planning profile of the real file re-run on a feature-001 database after the
upgrade, research.md R16; real numbers are recorded by task T036.)

Invariants (tested):
- `rows_read == rows_loaded + rows_excluded` (unchanged);
- `rows_excluded == sum(excluded_by_reason.values())`; all seven reason keys always present;
- `rows_loaded == matches_inserted + matches_updated + matches_unchanged` (unchanged);
- all nine rule ids always present in `violations_by_rule`, zeros included;
- `excluded_by_reason.contract_violation <= sum(violations_by_rule.values())`, and both are 0
  together;
- `contract_version` equals `data_contract.CONTRACT_VERSION` and the stored `ingest_runs` row.

Logs (stderr, INFO, logger `football_stats.*`): one line `contract rule=<id> violations=<n>` per rule
(zeros included) and one line
`converge matches_removed=<n> excluded_rows_removed=<n> teams_removed=<n> leagues_removed=<n>`.
Asserted with `caplog` by `test_run_report_and_record_contract_metadata` and
`test_rerun_removes_now_violating_matches`. The generic invariants above are asserted for every
fixture by `test_report_invariants_hold[*]` (unit) and for the CLI report by
`test_cli_ingest_report_matches_schema`.

## Exit codes (addition)

| Code | `error` | When | DB changed? |
|---|---|---|---|
| 9 | `contract_schema_error` | the frame given to the contract does not match its declared columns/dtypes, or a contract check failed to evaluate (programming error, research.md R14) | no (raised before the load) |
| 10 | `clock_skew` | the database server's UTC date is earlier than the run date (the app and DB clocks disagree), detected right after taking the lock (research.md R8) | no (raised before any write) |

## Structure command

`alembic upgrade head` now ends at revision `0003_contract_constraints` (after `0002_contract_run_metadata`). Applying it to a feature-001
database that contains contract-violating matches succeeds (constraints are `NOT VALID`); applying it
again is a no-op.
