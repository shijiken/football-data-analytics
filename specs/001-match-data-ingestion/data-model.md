# Data Model: Match Data Ingestion

**Feature**: `001-match-data-ingestion` | **Date**: 2026-10-11 | **Plan**: [plan.md](./plan.md)

All tables live in the `public` schema and are created by Alembic revision
`0001_initial_schema` (FR-009). Decisions referenced as R# are in [research.md](./research.md).

```text
ingest_runs 1 ──< excluded_rows
leagues     1 ──< matches >── 1 teams (home)
                          >── 1 teams (away)
```

## 1. Tables

### 1.1 `leagues` (FR-007)

| Column | Type | Null | Constraint |
|---|---|---|---|
| `league_code` | `text` | NO | **PK**; `CHECK (league_code ~ '^[A-Z0-9]{1,10}$')` |

Source: `Division`, trimmed and upper-cased. Name/country are not stored (R26).

### 1.2 `teams` (FR-004, FR-007, SC-006)

| Column | Type | Null | Constraint |
|---|---|---|---|
| `team_id` | `integer GENERATED ALWAYS AS IDENTITY` | NO | **PK** |
| `team_key` | `text` | NO | **UNIQUE**; `CHECK (team_key <> '')` — normalized identity (R7) |
| `name` | `text` | NO | `CHECK (btrim(name) <> '')` — canonical display spelling (R7) |

A team is not tied to a league; the same team appears in any number of leagues over time
(spec edge case "promotion/relegation").

### 1.3 `matches` (FR-005…FR-008)

Keys and identity:

| Column | Type | Null | Constraint |
|---|---|---|---|
| `match_id` | `bigint GENERATED ALWAYS AS IDENTITY` | NO | **PK** |
| `league_code` | `text` | NO | **FK** → `leagues(league_code)` |
| `match_date` | `date` | NO | |
| `kickoff_time` | `time` | YES | Source `MatchTime`, "CET-1" per dataset README, stored without zone |
| `home_team_id` | `integer` | NO | **FK** → `teams(team_id)` |
| `away_team_id` | `integer` | NO | **FK** → `teams(team_id)`; `CHECK (home_team_id <> away_team_id)` (FR-008) |
| `source_sha256` | `char(64)` | NO | `CHECK (source_sha256 ~ '^[0-9a-f]{64}$')` — file this row was last loaded from |
| `source_row_number` | `integer` | NO | `CHECK (source_row_number >= 1)` — data-record index in that file |

- **UNIQUE** `matches_natural_key (league_code, match_date, home_team_id, away_team_id)` — FR-006
  duplicate definition and the upsert conflict target (FR-011).
- **INDEX** `matches_home_team_id_idx (home_team_id)`, `matches_away_team_id_idx (away_team_id)` —
  FK lookups and "matches of team X". League/date queries use the unique index (leading columns).

Result and statistics (all source columns except the five key columns are stored):

| Column | Source | Type | Null | Constraint |
|---|---|---|---|---|
| `ft_home_goals` | `FTHome` | `smallint` | NO | `>= 0` |
| `ft_away_goals` | `FTAway` | `smallint` | NO | `>= 0` |
| `ft_result` | `FTResult` | `char(1)` | NO | `IN ('H','D','A')` |
| `ht_home_goals` | `HTHome` | `smallint` | YES | `>= 0` |
| `ht_away_goals` | `HTAway` | `smallint` | YES | `>= 0` |
| `ht_result` | `HTResult` | `char(1)` | YES | `IN ('H','D','A')` |
| `home_elo` / `away_elo` | `HomeElo` / `AwayElo` | `double precision` | YES | |
| `home_form3` / `home_form5` | `Form3Home` / `Form5Home` | `smallint` | YES | `>= 0` |
| `away_form3` / `away_form5` | `Form3Away` / `Form5Away` | `smallint` | YES | `>= 0` |
| `home_shots` / `away_shots` | `HomeShots` / `AwayShots` | `smallint` | YES | `>= 0` |
| `home_shots_on_target` / `away_shots_on_target` | `HomeTarget` / `AwayTarget` | `smallint` | YES | `>= 0` |
| `home_fouls` / `away_fouls` | `HomeFouls` / `AwayFouls` | `smallint` | YES | `>= 0` |
| `home_corners` / `away_corners` | `HomeCorners` / `AwayCorners` | `smallint` | YES | `>= 0` |
| `home_yellow_cards` / `away_yellow_cards` | `HomeYellow` / `AwayYellow` | `smallint` | YES | `>= 0` |
| `home_red_cards` / `away_red_cards` | `HomeRed` / `AwayRed` | `smallint` | YES | `>= 0` |
| `odd_home` / `odd_draw` / `odd_away` | `OddHome` / `OddDraw` / `OddAway` | `double precision` | YES | |
| `max_odd_home` / `max_odd_draw` / `max_odd_away` | `MaxHome` / `MaxDraw` / `MaxAway` | `double precision` | YES | |
| `odd_over25` / `odd_under25` | `Over25` / `Under25` | `double precision` | YES | |
| `max_odd_over25` / `max_odd_under25` | `MaxOver25` / `MaxUnder25` | `double precision` | YES | |
| `handicap_size` | `HandiSize` | `double precision` | YES | |
| `handicap_odd_home` / `handicap_odd_away` | `HandiHome` / `HandiAway` | `double precision` | YES | |
| `cluster_lth`, `cluster_lta`, `cluster_vhd`, `cluster_vad`, `cluster_htb`, `cluster_phb` | `C_LTH` … `C_PHB` | `double precision` | YES | |

No season column (R15).

### 1.4 `ingest_runs` (FR-001, FR-010)

Append-only audit log; one row per **successful** run (a failed run rolls back and leaves no row,
R11).

| Column | Type | Null | Constraint |
|---|---|---|---|
| `run_id` | `bigint GENERATED ALWAYS AS IDENTITY` | NO | **PK** |
| `started_at` | `timestamptz` | NO | |
| `finished_at` | `timestamptz` | NO | `CHECK (finished_at >= started_at)` |
| `source_url` | `text` | NO | URL or `file://` path the bytes came from |
| `source_sha256` | `char(64)` | NO | `CHECK (~ '^[0-9a-f]{64}$')` |
| `source_bytes` | `bigint` | NO | `CHECK (>= 0)` |
| `raw_path` | `text` | NO | content-addressed raw copy (R13) |
| `rows_read` | `integer` | NO | `CHECK (>= 0)` |
| `rows_loaded` | `integer` | NO | `CHECK (>= 0)` |
| `rows_excluded` | `integer` | NO | `CHECK (>= 0)` |
| `excluded_by_reason` | `jsonb` | NO | object `{reason_code: count}`; all six codes present, zero allowed |
| `matches_inserted` | `integer` | NO | `CHECK (>= 0)` |
| `matches_updated` | `integer` | NO | `CHECK (>= 0)` |
| `matches_unchanged` | `integer` | NO | `CHECK (>= 0)` |

Table-level checks:
- `CHECK (rows_read = rows_loaded + rows_excluded)` — FR-010 / SC-003 enforced by the database.
- `CHECK (rows_loaded = matches_inserted + matches_updated + matches_unchanged)`.

### 1.5 `excluded_rows` (FR-013, US3)

This is the constitution's `rejected_rows` table (Principle I) under the spec's terminology
("excluded"); one name is used throughout code, schema and docs to avoid a split vocabulary.

| Column | Type | Null | Constraint |
|---|---|---|---|
| `excluded_row_id` | `bigint GENERATED ALWAYS AS IDENTITY` | NO | **PK** |
| `run_id` | `bigint` | NO | **FK** → `ingest_runs(run_id)`; first run that recorded the row (R12) |
| `source_sha256` | `char(64)` | NO | `CHECK (~ '^[0-9a-f]{64}$')` |
| `source_row_number` | `integer` | NO | `CHECK (>= 1)` |
| `reason` | `text` | NO | `CHECK (reason IN ('missing_field','invalid_date','missing_result','invalid_value','same_team','duplicate'))` |
| `detail` | `text` | NO | human-readable specifics (column names, raw value, "duplicate of source row N") |
| `raw_record` | `jsonb` | NO | `{source column name: original string or null}` for **every** column in the file, values untrimmed |

- **UNIQUE** `excluded_rows_source_key (source_sha256, source_row_number)` — re-runs of the same file
  upsert instead of duplicating (FR-011).
- **INDEX** `excluded_rows_run_id_idx (run_id)`.

## 2. Source column contract (FR-002)

The **expected columns** are exactly the 48 header names in research.md R1, in any order. The
header check fails (exit 4, DB untouched) if any is missing or any header name occurs twice, and the
error message lists every missing column. Extra columns are allowed (warning logged).

Single source of truth in code: `src/football_stats/source_contract.py` holds, per source column:
`source_name`, `db_column`, `kind` (`key` | `required` | `optional`), `parser` (`text`, `date`,
`time`, `count`, `decimal`, `result`). A test asserts this table and the migrated `matches` table
have the same column set and types (`test_source_contract_matches_db_columns`).

### 2.1 Parsers (strict, regex-first, no locale)

All values are first stripped of surrounding whitespace; empty → null. Regexes use `[0-9]`, never
`\d` (which is Unicode-aware in both Rust regex and Python `re` and would accept e.g. `٣`, which the
subsequent cast cannot convert — that would break constraint parity).

| Parser | Accepts | Result | Otherwise |
|---|---|---|---|
| `date` | `^[0-9]{4}-[0-9]{2}-[0-9]{2}$` and a real calendar date | `date` | `invalid_date` |
| `time` | `^[0-9]{2}:[0-9]{2}:[0-9]{2}$`, `00–23:00–59:00–59` | `time` | `invalid_value` |
| `count` | `^[0-9]+(\.0+)?$`, value ≤ 32767 | `smallint` | `invalid_value` (covers negatives, fractions, `nan`, non-ASCII digits such as `٣`) |
| `decimal` | `^-?[0-9]+(\.[0-9]+)?$` | `float64` | `invalid_value` (covers `nan`, `inf`, `1e5`, `,` decimals) |
| `result` | `H`, `D`, `A` | `char(1)` | `invalid_value` |
| `league` | after upper-casing: `^[A-Z0-9]{1,10}$` | `text` | `invalid_value` |
| `team` | any non-blank text | name + key (R7) | blank → `missing_field` |

### 2.2 Required vs optional (FR-005)

- **Required (key)**: `Division`, `MatchDate`, `HomeTeam`, `AwayTeam` → missing ⇒ `missing_field`.
- **Required (result)**: `FTHome`, `FTAway`, `FTResult` → missing ⇒ `missing_result`.
- **Optional**: all other 41 columns → missing ⇒ stored as NULL, row still loaded.

## 3. Validation rules and constraint parity

Rule precedence is defined in research.md R6. **Constraint parity**: every DB constraint that could
reject a row produced by the pipeline has a validation rule that excludes that row first, so a data
problem becomes an excluded row, never a failed run.

| DB constraint | Validation rule (reason) | Unit test | Integration test |
|---|---|---|---|
| `leagues.league_code` regex | `league` parser (`invalid_value`) | `test_invalid_value_excluded_names_column[division]` | `test_constraint_rejects_bad_league_code` |
| `teams.team_key <> ''`, `name` non-blank | blank team (`missing_field`) | `test_missing_required_field_excluded[HomeTeam]` | `test_constraint_rejects_blank_team` |
| `matches.home_team_id <> away_team_id` | `same_team` | `test_same_team_after_normalization_excluded` | `test_constraint_rejects_home_equals_away` |
| `matches_natural_key` UNIQUE | `duplicate` | `test_duplicate_keeps_first_excludes_rest` | `test_constraint_rejects_duplicate_natural_key` |
| `NOT NULL` on date/teams/FT | `missing_field` / `missing_result` | `test_missing_required_field_excluded`, `test_missing_result_excluded` | `test_constraint_rejects_null_required` |
| `count` columns `>= 0` / smallint range | `count` parser (`invalid_value`) | `test_invalid_value_excluded_names_column[negative,fraction,overflow]` | `test_constraint_rejects_negative_goals` |
| `ft_result`/`ht_result` `IN ('H','D','A')` | `result` parser (`invalid_value`) | `test_invalid_value_excluded_names_column[result]` | `test_constraint_rejects_bad_result` |
| FKs `league_code`, `home/away_team_id` | loader inserts leagues/teams before matches in the same transaction | — | `test_constraint_rejects_unknown_league`, `test_constraint_rejects_unknown_team` |

## 4. Normalization (FR-004)

- `team_key = casefold(collapse_ws(strip(NFC(raw))))`.
- Display `name` = most frequent `collapse_ws(strip(NFC(raw)))` among accepted rows for that key;
  ties → smallest code-point string (R7).
- `league_code = upper(strip(raw))`.

## 5. Lifecycle / state transitions

A source record moves through these states within one run (no persisted state machine):

```text
read ──► header-checked ──► parsed ──► { accepted ──► upserted into matches
                                       { excluded ──► upserted into excluded_rows
```

- `rows_read` = records after the header (an empty line is a record).
- `rows_loaded` = accepted records (each upserted: inserted, updated, or unchanged).
- `rows_excluded` = excluded records; `rows_read = rows_loaded + rows_excluded` is checked in Python
  before the transaction (explicit `AccountingError`, exit 8 — not `assert`, which `python -O` strips)
  **and** by the `ingest_runs` CHECK.

Upsert semantics on re-run (R11, R12):

| Entity | Conflict target | On conflict |
|---|---|---|
| leagues | `league_code` | do nothing |
| teams | `team_key` | update `name` only if different |
| matches | `matches_natural_key` | update all non-key columns (incl. `source_sha256`, `source_row_number`) only if any differs — so the same file updates nothing, and a new file version re-stamps every match it contains (research.md R12) |
| excluded_rows | `(source_sha256, source_row_number)` | update `reason`, `detail`, `raw_record` only if different; keep `run_id` |

Rows are never deleted by ingestion in this feature (a match that disappears from a newer source
version stays in the database; documented limitation).
