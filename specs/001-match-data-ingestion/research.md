# Research: Match Data Ingestion

**Feature**: `001-match-data-ingestion` | **Date**: 2026-10-11 | **Plan**: [plan.md](./plan.md)

Every technology choice and every non-obvious design rule is recorded here as
**Decision / Rationale / Alternatives rejected**. Facts about the data source were measured on
2026-10-11 against the pinned commit below (profiling scripts were run in a scratch directory;
the dataset was not added to the repository).

---

## R1. Data source and pinning

**Facts (measured)**

| Item | Value |
|---|---|
| Repository | `https://github.com/xgabora/Club-Football-Match-Data` (renamed from `Club-Football-Match-Data-2000-2025`; the old URL redirects) |
| License | MIT |
| File | `data/Matches.csv` (the separate `EloRatings.csv` is out of scope per spec) |
| Pinned commit | `25882a58a736daf7ece3781940eac17ae1117a66` (HEAD of `main` on 2026-09-06; data last changed in `2e8632bc31`, "Update dataset through early 2026-27 season") |
| Default source URL | `https://raw.githubusercontent.com/xgabora/Club-Football-Match-Data/25882a58a736daf7ece3781940eac17ae1117a66/data/Matches.csv` |
| Size | 45,370,018 bytes |
| SHA-256 | `ef224cf2c252f07a842b3bcfd4ba5c718c25cedd8937ffa174a74b86b5ba4221` |
| Lines | 238,860 (1 header + 238,859 data records, one of which is an empty line at file line 230,559) |
| Encoding | Valid UTF-8, no BOM, `\n` line endings, `,` delimiter, no quoted fields observed |
| Columns | 48 (every non-empty line has exactly 48 fields) |
| Date range | 2000-07-28 … 2026-09-03 |
| Divisions | 38 codes: ARG AUT B1 BRA CHN D1 D2 DEN E0 E1 E2 E3 EC F1 F2 FIN G1 I1 I2 IRL JAP MEX N1 NOR P1 POL ROM RUS SC0 SC1 SC2 SC3 SP1 SP2 SUI SWE T1 USA |

**Real header (verbatim, in file order)**

```text
Division,MatchDate,MatchTime,HomeTeam,AwayTeam,HomeElo,AwayElo,Form3Home,Form5Home,Form3Away,Form5Away,FTHome,FTAway,FTResult,HTHome,HTAway,HTResult,HomeShots,AwayShots,HomeTarget,AwayTarget,HomeFouls,AwayFouls,HomeCorners,AwayCorners,HomeYellow,AwayYellow,HomeRed,AwayRed,OddHome,OddDraw,OddAway,MaxHome,MaxDraw,MaxAway,Over25,Under25,MaxOver25,MaxUnder25,HandiSize,HandiHome,HandiAway,C_LTH,C_LTA,C_VHD,C_VAD,C_HTB,C_PHB
```

**Decision**: The default `SOURCE_URL` is the commit-pinned raw URL above (overridable by the
`SOURCE_URL` environment variable). The SHA-256 of every downloaded file is computed and stored
with the run.

**Rationale**: A branch URL (`main`) changes whenever the author updates the dataset, which would
make "run twice on the same source" (FR-011, SC-005) and the expected numbers in
`quickstart.md` non-reproducible. A commit-pinned URL serves immutable content. Recording the
hash makes every run traceable to exact bytes (Principle I).

**Alternatives rejected**: `main` branch URL (non-reproducible); vendoring the 45 MB CSV in git
(bloats the repo, contradicts "download" in FR-001); enforcing an expected hash by default (would
break the documented `SOURCE_URL` override with no benefit, because commit-pinned content is
already immutable).

**Attribution**: The constitution requires "Gábor, A. (2025). *Club Football Match Data*." in
the README. The dataset README now suggests "Gábor, A. (2026)". The README will use the
constitution's wording plus the repository URL; changing the year is a constitution amendment, not
a plan decision.

## R2. Data profile → validation rules

Measured on the pinned file (238,859 data records):

| Observation | Count | Consequence for the design |
|---|---|---|
| Fully empty record (file line 230,559) | 1 | Must be counted in `rows_read` and excluded (`missing_field`), never skipped silently. Reader must not drop blank lines. |
| Missing `FTHome`/`FTAway`/`FTResult` (e.g. G1 2015-05-10 Niki Volos v OFI) | 4 | Excluded as `missing_result` (spec edge case). |
| Team names with trailing whitespace (`Ajax `, `Feyenoord `, `Kaiserslautern `, … 11 names) | 11 | Trim (FR-004). |
| Case-only variants (`Colon Santa Fe` / `Colon Santa FE`) | 1 pair | Case-insensitive identity key (FR-004). |
| Distinct raw team spellings → distinct normalized keys | 1,233 → 1,221 | Expected team count after normalization: 1,221. |
| Same team name in leagues of two different countries | 0 | Name-only team identity is safe for this dataset (see R7). |
| Duplicate `(Division, MatchDate, HomeTeam, AwayTeam)` before and after normalization | 0 | Duplicate rule is exercised by fixtures only. |
| Home = away after normalization | 0 | Same. |
| Invalid / non-ISO dates | 0 (all `YYYY-MM-DD`) | Strict single-format parser is sufficient. |
| `MatchTime` present | 107,373 (all `HH:MM:SS`, from 2012 on) | Optional; strict `HH:MM:SS`. Source says times are "CET-1"; stored as `time` without zone, documented. |
| Integer-valued columns written as floats (`3.0`) | all goal/stat/form columns | Parse `^\d+(\.0+)?$` → integer; anything else is `invalid_value`. |
| `FTResult` inconsistent with `FTHome`/`FTAway` | 0 | Not checked in this feature (spec limits checks to FR-002…FR-006); candidate for Phase 2 contracts. |
| HT fields partially present | 0 (all three present or all three absent) | No cross-field HT rule needed. |
| Odds equal to `0.0`, `HandiSize` = `-99.9` | a few | Source quirks; stored as-is. Domain rules on odds are Phase 2 (pandera). |
| `King\u0092s Lynn` (C1 control character U+0092 instead of an apostrophe) | 87 rows | Stored as-is (valid UTF-8). Repairing mojibake is name *mapping*, out of scope per spec. |

**Expected result of a full run on the pinned file** (used in `quickstart.md`): rows read
238,859; rows loaded 238,854; rows excluded 5 (`missing_result` 4, `missing_field` 1);
leagues 38; teams 1,221 (counted from accepted rows only). These numbers are a manual acceptance check, not an automated test
(tests never touch the network).

## R3. DataFrame library: polars

**Decision**: polars 1.x, all columns read as `String` (`infer_schema=False`).

**Rationale**: Reading everything as strings and parsing explicitly makes every conversion a
visible, testable rule; nothing is coerced silently (pandas turns integer columns with nulls into
floats and infers dtypes per chunk). polars is strictly typed, ships type stubs (mypy), errors on
ragged lines by default, and processes 239k × 48 in well under a second, leaving the SC-002 budget
to the database load.

**Alternatives rejected**: pandas (silent dtype inference, `skip_blank_lines=True` by default would
drop the empty record without accounting for it); stdlib `csv` + dicts (works, but rule
evaluation and grouping over 239k rows is slower and more code).

## R4. CSV reading strategy

**Decision**:
1. Read the header line with the stdlib `csv` module and check it (FR-002) **before** parsing the
   body. The check fails on missing columns and on duplicate column names; it names every missing
   column. Extra columns are allowed, logged as a warning, kept in `raw_record` for excluded rows,
   and otherwise ignored.
2. Read the body with `pl.read_csv(path, infer_schema=False, encoding="utf8")`; add
   `source_row_number` (1-based index of the data record; header is not counted; an empty line is
   a record).
3. Structural errors (invalid UTF-8, ragged rows, unterminated quotes) abort the run before the
   database is touched (exit code 4). They are file-level faults, not row-level ones.

**Rationale**: polars silently renames duplicate headers (`X_duplicated_0`), so the header must be
inspected raw. Checking the header first gives the clear, early error FR-002 asks for. Treating
structural corruption as fatal keeps row-level exclusion reasons meaningful and leaves the DB
unchanged (FR-012).

**Alternatives rejected**: `truncate_ragged_lines=True` (silently drops data, violates Principle I);
excluding ragged rows individually (requires a hand-written CSV tokenizer; no such rows exist in the
source; can be added in Phase 2 if needed).

The behaviour "an empty line is read as one all-null record" is pinned by a unit test
(`test_read_source_counts_blank_line_as_record`), so a polars upgrade that changes it fails the
gate instead of silently changing `rows_read`.

## R5. Validation approach for this feature

**Decision**: Hand-written, pure polars expressions in `validate.py` for exactly the checks in
FR-002…FR-006 plus the type parsing and FR-008 rules described in R6. No pandera.

**Rationale**: The spec states formal data-contract validation is Phase 2. The rules needed now are
few and must produce row-level reasons with a fixed precedence, which is direct to express in
polars.

**Alternatives rejected**: pandera now (YAGNI for this feature, and its failure-case output would
still need mapping to our reason codes); Great Expectations (heavy, report-oriented).

## R6. Exclusion reasons and precedence

**Decision**: Each excluded row gets exactly **one** reason code (plus a `detail` string), chosen by
the first failing rule in this fixed order:

| # | Code | Rule | Detail | Spec source |
|---|---|---|---|---|
| 1 | `missing_field` | `Division`, `MatchDate`, `HomeTeam` or `AwayTeam` is null or blank after trimming (an empty line fails here) | comma-separated missing column names, in contract order | FR-005 |
| 2 | `invalid_date` | `MatchDate` does not match `^\d{4}-\d{2}-\d{2}$` or is not a real calendar date | the raw value | FR-003 |
| 3 | `missing_result` | `FTHome`, `FTAway` or `FTResult` is null/blank | missing column names | FR-005, edge case "missing result" |
| 4 | `invalid_value` | any present value fails its column type/domain (see data-model "Column contract"): non-integral or negative count, non-decimal float (incl. `nan`, `inf`, exponent), time not `HH:MM:SS`, result not in `H`/`D`/`A`, division not `^[A-Z0-9]{1,10}$` after normalization | failing source column names | Principle I (nothing nulled silently), parity with DB CHECKs |
| 5 | `same_team` | normalized home team key = normalized away team key | the normalized key | FR-008 |
| 6 | `duplicate` | same `(league_code, match_date, home_key, away_key)` as an earlier accepted row | `duplicate of source row N` | FR-006 |

**Rationale**:
- One reason per row makes per-reason counts sum to `rows_excluded` (FR-010, SC-003). The detail
  string still names every failing field.
- Fixed precedence makes the outcome deterministic and testable (`test_reason_precedence`).
- Duplicates are evaluated last and only among otherwise-valid rows, so a broken row never
  "wins" over a good copy; the **lowest `source_row_number`** is kept.
- `invalid_value` and `same_team` exist because the database enforces the matching constraints
  (FR-008, CHECKs). Without a validation rule for every DB constraint, one bad row would abort the
  whole run (FR-012 rollback) instead of being excluded. This is the **constraint parity** rule
  (see data-model.md).
- Setting an unparseable optional value to NULL was rejected: it is silent data loss.

**Alternatives rejected**: multiple reasons per row (counts no longer sum; complicates FR-010);
keeping the last duplicate (order-dependent on file updates, less intuitive); a separate
`inconsistent_result` rule (not required by the spec, 0 occurrences; deferred to Phase 2).

## R7. Team name normalization and canonical display name

**Decision**:
- `team_key(name) = casefold(collapse_whitespace(strip(NFC(name))))`, where whitespace collapse
  replaces every run of Unicode whitespace with one space.
- Teams are identified by `team_key` (unique in DB). The stored display `name` is the
  whitespace-normalized (not casefolded) spelling that occurs **most often** among accepted rows
  (home + away occurrences) for that key; ties are broken by the lexicographically smallest
  code-point string.
- The mapping is computed on the ~1.2k distinct raw names in Python (`unicodedata`, `str.casefold`)
  and joined back to the frame.

**Rationale**: Satisfies FR-004 and SC-006 for formatting differences only, as the spec scopes it.
Casefold (not `lower`) handles `ß` etc. NFC prevents composed/decomposed `ü` from creating two
teams. The display rule is a pure function of the input, so the same file always yields the same
names (FR-011). Measured: no team name is shared by clubs of two countries, so name-only identity
is safe for this dataset.

**Alternatives rejected**: storing the lowercased name only (ugly display, loses the source's
capitalization); first-seen spelling (depends on row order); title-casing (`Psv`, `Afc` would be
wrong); a manual alias table for different names of the same club (explicitly out of scope).

## R8. Duplicate key

**Decision**: `(league_code, match_date, home_team_key, away_team_key)` in Python and
`UNIQUE (league_code, match_date, home_team_id, away_team_id)` in the database.

**Rationale**: Exactly FR-006. The same key is the upsert conflict target, so in-file duplicates and
re-run duplicates are handled by one definition (FR-011). Measured: 0 collisions in the real data,
also 0 on `(match_date, home, away)` alone.

**Alternatives rejected**: full-row equality (two copies differing only in odds would both load as
the same fixture); including `kickoff_time` (nullable; two copies with/without time would both load).

## R9. Database access: psycopg 3, no ORM

**Decision**: `psycopg[binary]` 3.2.x for the pipeline. SQL is written explicitly in `load.py`.
SQLAlchemy is present only as Alembic's dependency.

**Rationale**: The load is a handful of set-based statements (COPY into a temp table, three
`INSERT … ON CONFLICT`). psycopg 3 has a native COPY API, typed, and is the driver Alembic will also
use (`postgresql+psycopg://`). An ORM adds nothing for bulk loads and hides the SQL that this
project wants to practise.

**Alternatives rejected**: SQLAlchemy ORM/Core for the load (abstraction without need); psycopg2
(legacy, no typed API); `pandas.to_sql`/`polars.write_database` (row-by-row or no upsert control).

## R10. Migrations: Alembic with hand-written SQL

**Decision**: Alembic 1.16.x, `script_location = migrations`, revisions hand-written (no
autogenerate, since there are no ORM models), DDL expressed with `op.execute()` of plain SQL.
Revision ids are fixed, sequential strings (`0001_initial_schema`). `alembic upgrade head` is the
single structure command (FR-009); `downgrade` is implemented and tested.

**Rationale**: Gives versioning, ordering, the `alembic_version` bookkeeping, idempotent
`upgrade head` (US2-3) and transactional DDL without writing a migration runner. Plain SQL keeps the
schema readable and lets us use Postgres features (CHECK, partial constraints) directly.

**Alternatives rejected**: ordered `.sql` files + a home-made runner (reimplements version
tracking, ordering and locking — more code to test); `dbmate`/Flyway (non-Python binaries, another
container); `CREATE TABLE IF NOT EXISTS` in app code (not versioned; violates FR-009).

## R11. Load strategy and atomicity (FR-011, FR-012)

**Decision**: All database writes of a run happen in **one transaction**:

1. `SELECT pg_try_advisory_xact_lock(<constant>)`; if false → `ConcurrentRunError`, exit 5.
2. `CREATE TEMP TABLE stg_matches (…) ON COMMIT DROP`; `COPY stg_matches FROM STDIN (FORMAT csv)`
   streaming the accepted rows written by polars.
3. `INSERT INTO leagues … SELECT DISTINCT … ON CONFLICT DO NOTHING`.
4. `INSERT INTO teams (team_key, name) … ON CONFLICT (team_key) DO UPDATE SET name = EXCLUDED.name
   WHERE teams.name IS DISTINCT FROM EXCLUDED.name`.
5. `INSERT INTO matches … SELECT … JOIN teams … ON CONFLICT (league_code, match_date,
   home_team_id, away_team_id) DO UPDATE SET <all non-key columns> WHERE (<old cols>) IS DISTINCT
   FROM (<new cols>) RETURNING (xmax = 0) AS inserted` → counts inserted / updated / unchanged
   (unchanged = accepted − returned rows).
6. `INSERT INTO ingest_runs (…all counts…, finished_at = clock_timestamp()) RETURNING run_id`.
   Inserted only now, with complete counts, so its CHECK constraints hold at insert time and no
   UPDATE is needed.
7. `COPY` excluded rows into a temp table, then `INSERT INTO excluded_rows … ON CONFLICT
   (source_sha256, source_row_number) DO UPDATE SET reason, detail, raw_record` (run_id keeps the
   first run that recorded the row).
8. `COMMIT`. Any exception → `ROLLBACK`, error logged, non-zero exit.

**Rationale**: A single transaction is the simplest correct implementation of FR-012 (Postgres DDL
and DML are transactional; temp tables vanish on rollback). Upserts on natural keys give FR-011
without truncation. The advisory lock prevents two concurrent runs from interleaving upserts
(deadlock risk on the unique index) and is retry-safe (Principle II). COPY keeps 239k rows well
inside SC-002. Cleaning before opening the transaction keeps the lock/transaction short and means
FR-002/parse failures never touch the DB.

A failed run leaves **no** trace in the database (not even an `ingest_runs` row) — this is what
FR-012 literally requires ("previous state"). The failure is visible in the logs and exit code.

**Alternatives rejected**: truncate-and-reload (not incremental-friendly, destroys excluded-row
history, larger blast radius); staging-table swap (`ALTER TABLE … RENAME`; heavier, breaks FKs);
per-batch commits (violates FR-012); recording failed runs in a separate transaction (changes DB
state on failure, contradicting FR-012).

## R12. What "same state" means (FR-011, SC-005)

**Decision**: After a second run on the same file, the contents of `leagues`, `teams`, `matches`
and `excluded_rows` — **including surrogate ids** — are identical to after the first run.
`ingest_runs` is an append-only audit log and gains one row per successful run (with identical
counts). `excluded_rows.run_id` points to the first run that recorded the row.

Identity sequences may advance on conflicting inserts (Postgres evaluates the identity default
before the conflict check); sequence positions are not part of the contract.

**Rationale**: Makes FR-011 precisely testable (`test_second_run_leaves_identical_state` compares
full ordered table snapshots). Keeping a run log is required by FR-010/FR-013 and does not create
duplicate leagues/teams/matches/excluded rows.

**Alternatives rejected**: keying excluded rows by `(run_id, row)` (duplicates on every re-run,
violates FR-011); deleting and re-inserting excluded rows each run (changes ids and run links).

## R13. Raw layer: content-addressed file on disk

**Decision**: The downloaded bytes are streamed to `RAW_DATA_DIR/.partial-<pid>`, hashed while
streaming, then atomically renamed to `RAW_DATA_DIR/matches-<sha256>.csv` and made read-only
(`0o444`). If the target already exists, the partial file is discarded. The path, URL, size and hash
are stored in `ingest_runs`. A local `--source-file` is copied the same way.

**Rationale**: FR-001 asks for an unmodified copy of the raw file per run; content addressing makes
re-runs reuse the same copy (no growth), and the atomic rename guarantees an interrupted download
never leaves a file that looks complete. The raw layer stays rebuildable input for the clean tables
(Principle II).

**Alternatives rejected**: a `raw.match_rows` table with 48 text columns (duplicates the file in the
DB, not byte-identical, extra ~60 MB per version; can be added when Phase 3 dbt needs raw data in
SQL); timestamped copies (grow per run with identical content).

## R14. Download client: stdlib `urllib`

**Decision**: `urllib.request.urlopen(url, timeout=60)` streaming 1 MiB chunks; verify byte count
against `Content-Length` when present; HTTP errors, timeouts and short reads raise
`SourceUnavailableError` (exit 3). `file://` URLs work with the same code path. No automatic retry.

**Rationale**: One GET of one file needs no third-party HTTP client. Retries belong to the
orchestrator (Phase 4). The run is idempotent, so a manual retry is safe.

**Alternatives rejected**: `httpx`/`requests` (dependency without need); in-process retry with
backoff (YAGNI now; masks failures during development).

## R15. Seasons are not modelled in this feature

**Decision**: No `season` column. Acceptance scenario US1-2 ("matches for a league and season") is
satisfied by filtering `league_code` and a `match_date` range (e.g. `E0` season 2015/16 =
`2015-07-01 … 2016-06-30`), documented in quickstart.md and covered by
`test_query_by_league_and_date_range`.

**Rationale**: Season boundaries differ by league (ARG, BRA, CHN, JAP, USA, NOR, SWE, FIN, IRL play
calendar-year seasons; others July–June; some switched formats during 2000–2026). Deriving a wrong
season would be silently wrong data. Season derivation belongs in Phase 3 marts, where it can be
tested per league.

**Alternatives rejected**: `season = year if month >= 7 else year - 1` for all leagues (wrong for
calendar-year leagues); a static per-league season-format table (error-prone data entry, not
required by any FR).

## R16. Column types

**Decision**: goals, shots, fouls, corners, cards, form points → `smallint` with `CHECK (x >= 0)`;
Elo, odds, handicap, cluster probabilities → `double precision`; `ft_result`/`ht_result` →
`char(1)` with `CHECK IN ('H','D','A')`; date → `date`; kick-off → `time`.

**Rationale**: Integers are counts; smallint is ample (max observed 145). Odds/Elo are analytic
measurements, not money; double precision is exact enough for the 2–4 decimal source values and is
what analytics tools expect. Every CHECK has a matching validation rule (constraint parity, R6).

**Alternatives rejected**: `numeric` everywhere (slower, needs Decimal handling in polars, no
benefit for analytics); `integer` for counts (wider than needed).

## R17. Configuration

**Decision**: Environment variables read once into a frozen dataclass in `config.py`:
`DATABASE_URL` (required, `postgresql://…`), `SOURCE_URL` (default R1), `RAW_DATA_DIR` (default
`data/raw`), `LOG_LEVEL` (default `INFO`). Compose builds `DATABASE_URL` from `POSTGRES_*` values
in `.env` (git-ignored); `.env.example` is committed with placeholder values.

**Rationale**: Constitution IV (config from env, no committed secrets). Four values do not justify
pydantic-settings.

**Alternatives rejected**: pydantic-settings (dependency for 4 strings); config files (another
format to keep in sync with Compose).

## R18. CLI and output

**Decision**: `argparse` entry point `football-stats` with subcommands `ingest [--source-file PATH]`
and `verify [--sample N] [--seed S] [--source-file PATH]`. Logs go to **stderr** via `logging`;
the run report is written to **stdout** as one JSON document (`sys.stdout.write`, not `print`).
Exit codes are fixed (contracts/cli.md).

**Rationale**: Machine-readable stdout lets tests and scripts assert on the report without parsing
logs; logging satisfies the structured-logging standard. argparse is stdlib.

**Alternatives rejected**: Typer/Click (dependency for two subcommands); report only in logs (hard
to assert on, mixes with other lines).

## R19. Type checking: mypy `--strict`

**Decision**: mypy 1.x in strict mode over `src/` and `tests/`; `ignore_missing_imports` only for
`testcontainers.*`.

**Rationale**: Constitution requires a static type checker; mypy strict is the most widely known,
and polars/psycopg ship inline types.

**Alternatives rejected**: pyright (equally valid; mypy chosen for ubiquity and simpler CI install
via uv).

## R20. Lint and format: ruff, with rules that enforce the constitution

**Decision**: `ruff format` + `ruff check` with `select = ["E","F","W","I","B","UP","SIM","T20",
"BLE","S110","D","PL","RUF"]`, pydocstyle `google` convention; `D` and `PL` relaxed in `tests/` and
`migrations/`. Line length 100.

**Rationale**: `T20` bans `print` (structured logging), `BLE001`/`E722` ban blind/bare `except`,
`S110` bans `try/except/pass` (no swallowed exceptions), `D1xx` require public docstrings. The
standards become machine-checked instead of review-checked.

## R21. Test strategy and determinism

**Decision**:
- **pytest 8**. `tests/unit/` (pure logic, plus local-file reading) and `tests/integration/`
  (PostgreSQL, HTTP loopback, CLI subprocess).
- **testcontainers-python 4.x** `PostgresContainer("postgres:17.6-alpine")`, session-scoped. A
  template database is migrated once with `alembic upgrade head`; each test gets a fresh database via
  `CREATE DATABASE test_<n> TEMPLATE football_template` and drops it afterwards. Migration tests use
  an empty database. No test depends on another test's data.
- **No external network**: all data comes from `tests/fixtures/*.csv`; download tests use `file://`
  URLs and an `http.server` bound to `127.0.0.1` on an ephemeral port inside the test process. Unit
  tests run with **pytest-socket** `--disable-socket`, so any socket use there fails the gate.
- **Order independence**: **pytest-randomly** shuffles test order with a fixed seed
  (`--randomly-seed=20261011` in `scripts/check.sh`), so the order is deterministic in
  the gate but not alphabetical; developers can pass other seeds to hunt order dependence.
- **No time dependence**: assertions never compare timestamps; `started_at`/`finished_at` are
  excluded from snapshots. `verify` sampling uses `random.Random(seed)`.
- **Independent oracle** for SC-004: the integration test re-reads fixture CSVs with the stdlib `csv`
  module (not polars) and compares every loaded match.

**Rationale**: Constitution III (real Postgres, not mocks); planner rules (no network, fixed seeds,
no order reliance). Template databases give isolation in ~100 ms per test instead of a container per
test.

**Alternatives rejected**: one container per test (slow); TRUNCATE between tests (leaks sequence
state, easy to forget a table); SQLite (forbidden); hypothesis property tests (YAGNI for the size of
the normalization rules — parametrized examples suffice); pytest-socket on integration tests (Docker
host/port mapping varies across machines and would make the gate flaky — integration tests are
network-free by construction instead).

## R22. Containers and Compose

**Decision**:
- `db`: `postgres:17.6-alpine`, healthcheck `pg_isready`, named volume, port bound to
  `127.0.0.1:${POSTGRES_PORT:-5432}`.
- `migrate`: app image, `alembic upgrade head`, `depends_on: db (service_healthy)`, one-shot.
- `ingest`: app image, `football-stats ingest`, `depends_on: migrate
  (service_completed_successfully)`, named volume `rawdata` at `/app/data/raw`, `profiles: ["ingest"]`.
  A named volume (not a bind mount) avoids host/container UID mismatches on Linux CI runners; the
  volume is initialised from the image directory owned by the non-root user.
- App image: `python:3.12.11-slim-bookworm`, `pip install uv==0.12.5`, `uv sync --frozen --no-dev`,
  non-root user. Single stage (multi-stage is Phase 4).
- `docker compose up -d` starts `db` + `migrate` (US2-1, US2-2). `docker compose run --rm ingest`
  starts dependencies as needed and loads the data. SC-001 path: `cp .env.example .env` then
  `docker compose run --rm ingest` (two commands).

**Rationale**: Constitution IV (one command, pinned images, env config). Putting `ingest` behind a
profile stops every `docker compose up` from re-downloading 45 MB, while the full stack still comes
up with one command.

**Alternatives rejected**: running migrations inside the ingest command (couples structure and data,
violates FR-009's "one command for structure"); `latest` tags (unpinned); Postgres 16 (17 is current
stable with no relevant drawbacks).

## R23. CI

**Decision**: `.github/workflows/ci.yml` on `ubuntu-24.04`: `astral-sh/setup-uv` (uv 0.12.5) →
`scripts/check.sh` → `docker build` → `scripts/smoke_compose.sh` (Compose migrate twice + ingest a
fixture file through the container; no network data).

**Rationale**: Constitution: CI runs lint → type check → unit → integration → image build. The
Compose smoke test exercises US2 and the container path of US1 automatically.

## R24. SC-004 spot check on real data: `verify` subcommand

**Decision**: `football-stats verify --sample 20 --seed 42` re-reads the raw file for the latest run
with the stdlib `csv` module, picks N accepted rows with `random.Random(seed)`, and compares date,
home team, away team and full-time score with the database row linked by
`(source_sha256, source_row_number)`. It prints a JSON report and exits 6 on any difference.

**Rationale**: SC-004 must be checkable on the real dataset, which tests cannot download. A seeded
sample is reproducible. The integration test applies the same comparison to **all** fixture rows.

**Alternatives rejected**: manual psql comparison (48-column `\copy` into a temp table; error-prone);
no real-data check (SC-004 would be unverified).

## R25. Performance measurement (SC-001, SC-002)

**Decision**: Measured, not asserted in the gate. `quickstart.md` step "Timed full run" records
`time docker compose run --rm ingest` on the developer laptop; the measured value is written to the
README (constitution: performance claims are measured). Timing assertions are excluded from tests
because wall-clock limits are machine-dependent and flaky.

## R26. League metadata

**Decision**: `leagues` stores only `league_code` in this feature. No static name/country lookup.

**Rationale**: The spec marks name/country as optional; no FR/SC consumes them. Adding columns that
are always NULL, or hand-entering 38 names, is YAGNI. Phase 3 marts can add a seed table.
