# Football Stats

A small, well-tested data pipeline: it downloads the *Club Football Match Data* CSV (about 239,000
matches, 48 columns, 2000 to 2026), validates and cleans every row, and loads it into PostgreSQL
in a single transaction. Every input row ends up either in `matches` or in `excluded_rows` with the
reason it was excluded, and running the pipeline twice changes nothing.

This is Phase 1 of a larger learning project (data engineering practice first, analysis second).
The feature specification, plan and decisions live in
[`specs/001-match-data-ingestion/`](specs/001-match-data-ingestion/).

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
        |
        v
 load.py      ONE transaction: advisory lock, COPY into temp tables, INSERT ... ON CONFLICT
        |
        v
 PostgreSQL   leagues  teams  matches  excluded_rows  ingest_runs      (Alembic migration 0001)
```

Modules: `errors`, `config`, `source_contract` (leaves) - `extract`, `clean`, `validate`, `load`
(one per stage) - `pipeline` (orchestration) - `verify` (SC-004 spot check) - `cli`.

## Prerequisites

Docker with Compose v2. For development also Python 3.12 and [uv](https://docs.astral.sh/uv/)
0.12.5.

## Run it (two commands)

```bash
cp .env.example .env
docker compose run --rm ingest
```

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
| psycopg + hand-written SQL, no ORM (R9) | Set-based COPY and upserts are the point of the exercise | SQL strings live in `load.py` |

## Known data quirks

Stored as-is in this feature (domain rules are deferred to a later phase):

- `King\u0092s Lynn` contains a C1 control character (U+0092) instead of an apostrophe, and is
  the club's only spelling in the file (87 rows). Repairing it would be name mapping, which is
  out of scope.
- 8 odds values are `0.0`.
- 2 `HandiSize` values are `-99.9`.
- The file has a blank line at file line 230,559. It is counted as a record and excluded as
  `missing_field` (it is one of the 5 excluded rows).

## Measured numbers

Performance figures are measured, not estimated (constitution). They are recorded by task T053,
which runs [`quickstart.md`](specs/001-match-data-ingestion/quickstart.md) against the real pinned
dataset.

| Item | Value |
|---|---|
| SC-001 fresh checkout to loaded database (target under 10 min) | TODO(T053): not measured yet |
| SC-002 download + clean + load of the full file (target under 5 min) | TODO(T053): not measured yet |
| Machine (CPU / RAM / Docker) | TODO(T053) |
| Real counts: rows read / loaded / excluded | TODO(T053) (expected 238,859 / 238,854 / 5) |
| Real counts: leagues / teams | TODO(T053) (expected 38 / 1,221) |

## Repository layout

```text
src/football_stats/   the package (see Architecture)
migrations/           Alembic revisions (schema is changed only here)
tests/                unit/, integration/, fixtures/
scripts/              check.sh (gate), smoke_compose.sh
specs/                spec, plan, research, data model, contracts, tasks, decisions, reviews
```
