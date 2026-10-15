# Football Stats

A small, well-tested data pipeline: it downloads the *Club Football Match Data* CSV (about 239,000
matches, 48 columns, 2000 to 2026), validates and cleans every row, and loads it into PostgreSQL
in a single transaction. Every input row ends up either in `matches` or in `excluded_rows` with the
reason it was excluded, and running the pipeline twice changes nothing.

This is Phase 1 of a larger learning project (data engineering practice first, analysis second).
The feature specifications, plans and decisions live in
[`specs/001-match-data-ingestion/`](specs/001-match-data-ingestion/) (ingestion) and
[`specs/002-data-contract/`](specs/002-data-contract/) (data contract).

## Data source and attribution

Gábor, A. (2025). *Club Football Match Data*.
<https://github.com/xgabora/Club-Football-Match-Data> (MIT licence). The pipeline downloads
`data/Matches.csv` from a commit-pinned URL so results are reproducible
([research.md R1](specs/001-match-data-ingestion/research.md)). The dataset is never committed to
this repository (`data/` is git-ignored); `tests/fixtures/matches_real_sample.csv` holds 16
unchanged rows, see `tests/fixtures/SOURCES.txt`.

## Architecture

```text
 Matches.csv (GitHub, pinned commit)            tests: local fixture / file:// / loopback HTTP
        |
        v
 extract.py   stream + SHA-256 --> data/raw/matches-<sha256>.csv   (read-only, content-addressed)
        |                           header check (FR-002), structure check (field counts, UTF-8)
        v
 clean.py     strict parsers, team-name normalization              (pure: DataFrame in/out)
 validate.py  one exclusion reason per row, fixed precedence       (pure)
data_contract.py (pandera)  nine cross-field rules, versioned     (pure)
        |
        v
 load.py      ONE transaction: advisory lock, COPY into temp tables, INSERT ... ON CONFLICT
        |
        v
 PostgreSQL   leagues  teams  matches  excluded_rows  ingest_runs      (Alembic migrations 0001-0003)
```

Modules: `errors`, `config`, `source_contract` (leaves) - `extract`, `clean`, `validate`,
`data_contract`, `load` (one per stage) - `pipeline` (orchestration) - `verify` (SC-004 spot check) - `cli`.

## Prerequisites

Docker with Compose v2. For development also Python 3.12 and [uv](https://docs.astral.sh/uv/)
0.12.5.

## Run it (two commands)

```bash
cp .env.example .env
docker compose run --rm ingest
```

If port 5432 is already in use on your machine (for example by a local PostgreSQL), set
`POSTGRES_PORT` in `.env` to a free port such as `55432` before running. The containers talk to
each other on the internal network, so only host access to the database uses this port.

`docker compose up -d` starts only the database and applies the migration; the `ingest` job sits
behind a Compose profile so it never re-downloads 45 MB by accident. The command prints one JSON
report on stdout (logs go to stderr):

```json
{"status": "succeeded", "run_id": 1, "rows_read": 238859, "rows_loaded": 238854,
 "rows_excluded": 5, "excluded_by_reason": {"missing_field": 1, "missing_result": 4, "...": 0}, "...": "..."}
```

Other useful commands:

```bash
docker compose run --rm ingest football-stats verify --sample 20 --seed 42   # SC-004 spot check
docker compose run --rm migrate                                               # re-apply the schema (no-op at head)
docker compose down -v                                                        # remove containers and volumes
```

Exit codes, environment variables and report schemas:
[`contracts/cli.md`](specs/001-match-data-ingestion/contracts/cli.md). The manual acceptance run
is [`quickstart.md`](specs/001-match-data-ingestion/quickstart.md).

## Quality gate

```bash
uv sync --frozen
scripts/check.sh      # ruff format, ruff check, mypy --strict, unit tests, integration tests
scripts/smoke_compose.sh   # builds the image, migrates twice, ingests a fixture in a container
```

Integration tests run against a real PostgreSQL 17.6 started by testcontainers (no mocks, no
SQLite); each test gets a database cloned from a migrated template. Unit tests run with sockets
disabled. Test order is shuffled with a fixed seed. CI (`.github/workflows/ci.yml`) runs the same
gate, then the image build and the Compose smoke test.

## Design decisions and trade-offs

Full reasoning, with rejected alternatives, is in
[`research.md`](specs/001-match-data-ingestion/research.md).

| Decision | Why | Trade-off |
|---|---|---|
| One transaction for the whole load (R11) | A failed run leaves the database exactly as it was (FR-012) | A very large load holds locks for its duration; fine for 239k rows |
| Upsert on natural keys instead of truncate-and-reload (R11, R12) | Re-runs are idempotent and keep ids and excluded-row history | A match that disappears from a newer file is not deleted |
| Content-addressed, read-only raw file (R13) | The raw layer is unchanged and re-runs reuse the same copy | Needs disk space per distinct source version |
| Constraint parity (data-model section 3) | Every DB constraint has a validation rule that excludes the row first, so bad data becomes an excluded row, never a failed run | More rules to keep in sync (a test compares the contract with the schema) |
| Everything is read as text, then parsed explicitly (R3, R4) | Nothing is coerced silently; polars pads short rows, so a stdlib-csv structure check guards the field counts | One extra pass over the file (about a second) |
| One reason per excluded row, fixed precedence (R6) | Counts add up to `rows_excluded`, outcome is deterministic | The `detail` column names every failing field |
| No season column (R15) | Season boundaries differ per league; deriving them wrongly would be silent bad data | Queries use a `match_date` range; seasons belong in Phase 3 marts |
| pandera evaluates the contract as vectorized DataFrame-level checks (002 R2) | One declarative schema object; `validate(lazy=True)` returns every failing row of every rule in one pass (0.5 s worst case on 240k rows) | A 0.x dependency, so it is pinned `<0.35` and its behaviour is pinned by a test |
| Contract CHECK constraints added `NOT VALID`, parity proven rule by rule (002 R8) | The database rejects new or updated violators, and the upgrade still works on a database that already holds some | Old rows are not scanned; they are removed when their source file is re-ingested |
| Re-running the same file converges (002 R10) | An upgraded database becomes identical to a fresh build, in one transaction | Matches of other source files are never touched |
| Orphan teams and leagues are deleted (002 R11) | Otherwise an upgraded database differs from a fresh build | A global rule ("no match references it"), not file-scoped |
| "Identical" means content without surrogate ids (002 R12) | Identity values are never reused, so ids cannot match after deletes | `ingest_runs` is an audit log and is not compared |
| psycopg + hand-written SQL, no ORM (R9) | Set-based COPY and upserts are the point of the exercise | SQL strings live in `load.py` |

## Data contract

Contract version 1 ([`specs/002-data-contract/`](specs/002-data-contract/),
[`contracts/data-contract.md`](specs/002-data-contract/contracts/data-contract.md)) adds nine
cross-field rules on top of the per-column checks. A row that passed those checks but breaks a rule
is excluded with reason `contract_violation`; its `detail` lists every violated rule id. The
reasons now have seven values, in this precedence: `missing_field`, `invalid_date`,
`missing_result`, `invalid_value`, `same_team`, `contract_violation`, `duplicate`. A rule is
skipped when one of its inputs is missing, so sparse optional columns never cause exclusions.

| Rule id | What must hold |
|---|---|
| `ht_le_ft_home` | half-time home goals do not exceed full-time home goals |
| `ht_le_ft_away` | half-time away goals do not exceed full-time away goals |
| `ft_result_consistent` | the full-time result matches the full-time score |
| `ht_result_consistent` | the half-time result matches the half-time score |
| `sot_le_shots_home` | home shots on target do not exceed home shots |
| `sot_le_shots_away` | away shots on target do not exceed away shots |
| `odds_min` | every pre-match decimal odd (average and maximum) is at least 1.0 |
| `max_odds_ge_avg` | each maximum odd is at least the corresponding average odd |
| `date_in_range` | the match date is between 2000-01-01 and the run date (UTC) |

Every run records the contract version and the violations per rule in `ingest_runs` and in the JSON
report. The database enforces the same nine rules with `matches_contract_<rule id>` CHECK
constraints (added `NOT VALID`, so the upgrade works on a database that already contains violating
matches). Re-running `ingest` on the same file removes that file's matches that no longer pass, so
the database converges to what a fresh run would build. If a persistent validation of the
constraints is ever wanted it ships as a new Alembic revision; the tests only run
`VALIDATE CONSTRAINT` inside a rolled-back transaction, as a read-only proof.

Changing a rule means: bump `CONTRACT_VERSION`, add the matching Alembic revision, update this
table and add cases to `tests/contract_cases.py` (the gate fails otherwise).

## Known data quirks

Stored as-is by feature 001 (the data contract, below, now excludes the odds problems):

- `King\u0092s Lynn` contains a C1 control character (U+0092) instead of an apostrophe, and is
  the club's only spelling in the file (87 rows). Repairing it would be name mapping, which is
  out of scope.
- 8 odds values are `0.0`. Since contract version 1 these rows are excluded (`odds_min`), as are
  rows whose maximum odd is below the average odd (`max_odds_ge_avg`).
- 2 `HandiSize` values are `-99.9`.
- The file has a blank line at file line 230,559. It is counted as a record and excluded as
  `missing_field` (it is one of the 5 excluded rows).

## Measured numbers

Performance figures are measured, not estimated (constitution). They are recorded by task T053,
which runs [`quickstart.md`](specs/001-match-data-ingestion/quickstart.md) against the real pinned
dataset.

| Item | Value |
|---|---|
| SC-001 fresh checkout to loaded database (target under 10 min) | 23.6 s wall time for `docker compose run --rm ingest` on an empty database, base images already pulled (image build cached) |
| SC-002 download + clean + load of the full file (target under 5 min) | 18.7 s (`duration_seconds` in the run report, first run); idempotent re-run 8.9 s |
| Machine (CPU / RAM / Docker) | Apple M3 / 16 GB / Docker Engine 29.8.2 (Docker Desktop) |
| Real counts: rows read / loaded / excluded | 238,859 / 238,854 / 5 (4 `missing_result`, 1 `missing_field`) |
| Real counts: leagues / teams | 38 / 1,221 |

Measured 2026-10-11 against the pinned source (sha256 `ef224cf2…4221`). The same run also confirmed
the Premier League 2015/16 query returns 380 rows, a second ingest changes nothing (0 inserted,
0 updated, 238,854 unchanged), `verify --sample 20 --seed 42` reports no differences, and an
unreachable source exits 3 with the database unchanged.

## Repository layout

```text
src/football_stats/   the package (see Architecture)
migrations/           Alembic revisions (schema is changed only here)
tests/                unit/, integration/, fixtures/
scripts/              check.sh (gate), smoke_compose.sh
specs/                spec, plan, research, data model, contracts, tasks, decisions, reviews
```

## Measured numbers - data contract

Recorded by task T036, which ran [`quickstart.md`](specs/002-data-contract/quickstart.md) against
the real pinned dataset (sha256 `ef224cf2...4221`, re-downloaded and re-verified by each run).

| Item | Value |
|---|---|
| Run date (UTC) / contract version | 2026-10-15 / 1 |
| Rows read / loaded / excluded | 238,859 / 237,473 / 1,386 (`missing_field` 1, `missing_result` 4, `contract_violation` 1,381) |
| Leagues / teams | 38 / 1,221 |
| Machine (CPU / RAM / Docker) | Apple M3 / 16 GB / Docker Engine 29.8.2 (Docker Desktop) |
| SC-005 fresh full run (target under 5 min): wall time / `duration_seconds` | 17.1 s / 14.4 s (`time docker compose run --rm ingest` on an empty database: download, hash, clean, validate, load) |
| Converging re-run of the feature-001 database (238,854 matches): `matches_removed` | 1,381 (wall time 13.1 s, `duration_seconds` 10.5 s); 237,473 matches unchanged |
| Re-run of the converged database | 0 inserted, 0 updated, 0 removed, 237,473 unchanged (9.3 s) |

Violations per rule (rows read: 238,859):

| Rule id | Rows violating | Share of rows read |
|---|---:|---:|
| `ht_le_ft_home` | 0 | 0 % |
| `ht_le_ft_away` | 0 | 0 % |
| `ft_result_consistent` | 0 | 0 % |
| `ht_result_consistent` | 0 | 0 % |
| `sot_le_shots_home` | 7 | 0.0029 % |
| `sot_le_shots_away` | 13 | 0.0054 % |
| `odds_min` | 8 | 0.0033 % |
| `max_odds_ge_avg` | 1,357 | 0.5681 % |
| `date_in_range` | 0 | 0 % |
| rows with at least one violation | 1,381 (4 rows break two rules) | 0.5782 % |

No rule exceeds the 1 % review threshold; the largest, `max_odds_ge_avg`, is discussed in
[`decisions.md`](specs/002-data-contract/decisions.md) ("FR-011 real-data review"). The numbers equal
the planning profile of `research.md` R16 exactly. The nine constraints were also proven against the
whole converged table with `VALIDATE CONSTRAINT` inside a rolled-back transaction (all succeeded,
catalog unchanged), and `verify --sample 20 --seed 42` reports no differences.
