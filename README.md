# Football Stats

A small, well-tested data pipeline: it downloads the *Club Football Match Data* CSV (about 239,000
matches, 48 columns, 2000 to 2026), validates and cleans every row, and loads it into PostgreSQL
in a single transaction. Every input row ends up either in `matches` or in `excluded_rows` with the
reason it was excluded, and running the pipeline twice changes nothing. A dbt project then turns
the loaded matches into staging views and four analytical tables (team form, home advantage, goals
per season, Elo ratings).

This is Phases 1 to 3 of a larger learning project (data engineering practice first, analysis
second). The feature specifications, plans and decisions live in
[`specs/001-match-data-ingestion/`](specs/001-match-data-ingestion/) (ingestion),
[`specs/002-data-contract/`](specs/002-data-contract/) (data contract) and
[`specs/003-dbt-transforms/`](specs/003-dbt-transforms/) (analytics transforms).

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
        |
        v   transform/  (dbt 1.11: SQL models, data tests; reads only schema public)
 analytics_staging   stg_leagues  stg_teams  stg_matches                      (views)
        |
        v
 analytics_marts     mart_team_form  mart_league_season_home_advantage
                     mart_league_season_goals  mart_team_ratings              (tables, contracts)
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
{"status": "succeeded", "run_id": 1, "contract_version": 1, "rows_read": 238859,
 "rows_loaded": 237473, "rows_excluded": 1386,
 "excluded_by_reason": {"missing_field": 1, "missing_result": 4, "contract_violation": 1381, "...": 0},
 "violations_by_rule": {"max_odds_ge_avg": 1357, "...": 0}, "matches_inserted": 237473, "...": "..."}
```

After pulling a new version, rebuild first, otherwise an old image runs against the new schema:
`docker compose --profile ingest build` (or `docker compose run --rm --build ingest`). `migrate`
and `ingest` share one image tag (`football-stats:local`), so they cannot diverge.

Other useful commands:

```bash
docker compose run --rm ingest football-stats verify --sample 20 --seed 42   # SC-004 spot check
docker compose run --rm migrate                                               # re-apply the schema (no-op at head)
docker compose down -v                                                        # remove containers and volumes
```

Exit codes, environment variables and report schemas:
[`contracts/cli.md`](specs/001-match-data-ingestion/contracts/cli.md), with the data-contract
additions (new report fields, exit codes 9 and 10) in
[`specs/002-data-contract/contracts/cli.md`](specs/002-data-contract/contracts/cli.md). The manual acceptance run
is [`quickstart.md`](specs/001-match-data-ingestion/quickstart.md).

## Analytics transforms

The dbt project in [`transform/`](transform/) builds two schemas that dbt owns; `public` is only
read. Run it after an ingest, never while one is running (each model is built in its own
transaction, so a concurrent ingest could give two marts different snapshots, and the reconcile
tests would then fail the build):

```bash
docker compose run --rm ingest && docker compose run --rm transform
# on the host, with DBT_PG_* exported (copy them from .env.example into .env, then set -a; . ./.env; set +a):
uv run --frozen dbt build --project-dir transform --profiles-dir transform
```

`analytics_staging` holds three views (`stg_leagues`, `stg_teams`, `stg_matches`, which adds the
season, each side's result and points, and total goals). `analytics_marts` holds four tables with
enforced dbt contracts (types and primary keys), described in
[`contracts/marts.md`](specs/003-dbt-transforms/contracts/marts.md):

| Mart | One row per | Answers |
|---|---|---|
| `mart_team_form` | team per match | the team's result and points in each match, and its form (points, goals for and against) over its previous five matches; empty for a team's first match |
| `mart_league_season_home_advantage` | league-season | home wins, draws and away wins with shares, average home and away goals |
| `mart_league_season_goals` | league-season | total goals, goals per match, over 2.5 goals and both-teams-score counts and shares |
| `mart_team_ratings` | team | latest, peak and lowest pre-match Elo with dates; teams with no Elo have nulls |

**Season rule.** A match dated July to December belongs to season `YYYY/YY+1` (start year `YYYY`),
January to June to `YYYY-1/YY`, for every league: `2021-06-30` is `2020/21`, `2021-07-01` is
`2021/22`, and `2000-01-01` is `1999/00`. Calendar-year leagues (Argentina, Brazil, USA, ...) are
therefore split across two labels; `season_start_year` is the sortable form.

**Form window and ties.** A team's matches are ordered by `match_date`, then `match_id` (which
follows source-file order on a fresh load); the form covers the up to five matches strictly before
the current one. For peak and lowest Elo, ties go to the earliest date.

`dbt build` exits 0 when every model is built and all 75 nodes (7 models and 68 data tests: keys,
relationships, accepted values, and six singular invariants that recompute the marts from staging)
pass, 1 when a model or test fails, and 2 when dbt cannot run (a missing variable, no database).
**After a non-zero exit the analytics schemas are not trustworthy until the next exit-0 run:** a
mart whose own test failed stays published, and skipped models keep their previous contents. Rerun
once the cause is fixed; every model is a full rebuild from `public`, so the rerun converges to a
fresh build (tested on one database). See
[`contracts/transform.md`](specs/003-dbt-transforms/contracts/transform.md) and
[`research.md` R16, R18](specs/003-dbt-transforms/research.md).

Trade-off: the staging views depend on `public.leagues`, `teams` and `matches`, so a future
migration that drops or retypes a column a view selects (or `alembic downgrade base`) fails with
"other objects depend on it" until `DROP SCHEMA analytics_staging CASCADE` is run; the next
`dbt build` recreates it. Ingestion is not affected.

## Quality gate

```bash
uv sync --frozen
scripts/check.sh      # ruff format, ruff check, mypy --strict, unit tests, integration tests
scripts/smoke_compose.sh   # builds both images, migrates twice, ingests a fixture, runs the transform twice
```

Integration tests run against a real PostgreSQL 17.6 started by testcontainers (no mocks, no
SQLite); each test gets a database cloned from a migrated template. Unit tests run with sockets
disabled. Test order is shuffled with a fixed seed. The transform is tested inside the same gate:
the integration tests ingest a hand-designed fixture with the real pipeline, run `dbt build` as a
subprocess (nine invocations) and compare every mart value with hand-calculated expectations; each
singular test is shown to fail on a rolled-back corruption. The whole gate takes 45 s (`time scripts/check.sh`: 45.1 s, of which the integration step is 38 s; the baseline before this feature was 24 s)
on the machine below. CI (`.github/workflows/ci.yml`) runs the same gate, then both image builds
and the Compose smoke test.

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
| No season column in `public` (R15) | Season boundaries differ per league; deriving them wrongly would be silent bad data | Seasons are derived in the dbt staging layer (July rule, see Analytics transforms) |
| pandera evaluates the contract as vectorized DataFrame-level checks (002 R2) | One declarative schema object; `validate(lazy=True)` returns every failing row of every rule in one pass (0.5 s worst case on 240k rows) | A 0.x dependency, so it is pinned `<0.35` and its behaviour is pinned by a test |
| Contract CHECK constraints added `NOT VALID`, parity proven rule by rule (002 R8) | The database rejects new or updated violators, and the upgrade still works on a database that already holds some | Old rows are not scanned; they are removed when their source file is re-ingested |
| Re-running the same file converges (002 R10) | An upgraded database becomes identical to a fresh build, in one transaction | Matches of other source files are never touched |
| Orphan teams and leagues are deleted (002 R11) | Otherwise an upgraded database differs from a fresh build | A global rule ("no match references it"), not file-scoped |
| "Identical" means content without surrogate ids (002 R12) | Identity values are never reused, so ids cannot match after deletes | `ingest_runs` is an audit log and is not compared |
| psycopg + hand-written SQL, no ORM (R9) | Set-based COPY and upserts are the point of the exercise | SQL strings live in `load.py` |
| Dbt 1.11, not 1.12 (003 R1) | 1.11 is the newest line whose dependency tree is hashed wheels in `uv.lock`; 1.12 downloads an un-hashed binary at install time | Stays on a maintenance line; `<1.12` is pinned and tested |
| A separate `transform` image stage (003 R2) | dbt stays out of the app image and polars out of the transform image; one `uv.lock` for both | Two images to build; the smoke test checks they stay separate |
| Marts have dbt contracts and primary keys (003 R11) | A type or key drift fails the build, in the container too | No database foreign keys: they would couple ingestion DML to analytics or break dbt's table swap; `relationships` tests replace them |
| Invariants are recomputed independently and mutation-checked (003 R10) | Each singular test (LAG instead of a frame, `DISTINCT ON` instead of windows, re-aggregation) is shown to fail on a corrupted copy | Nine rolled-back corruption cases to maintain |
| No Alembic index for the transform (003 R14) | Measurement showed candidate indexes on `matches` do not help the transform; the one mart index lives in dbt model config, which every build applies | Mart indexes are versioned in dbt, not Alembic (justified in the plan's Complexity Tracking) |

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
transform/            dbt project: staging views, marts, data tests (feature 003)
scripts/              check.sh (gate), smoke_compose.sh, explain_transforms.sql (index measurements)
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

## Measured numbers - analytics transforms

Recorded by task T036 on 2026-10-18 against the full database left by features 001 and 002
(237,473 matches, Alembic head `0003_contract_constraints`). Machine: Apple M3 / 16 GB / Docker
Engine 29.8.2 (Docker Desktop), PostgreSQL 17.6 in the Compose `db` service, warm cache.

| Item | Value |
|---|---|
| SC-001 full build (target under 2 min): `time docker compose run --rm transform`, first / second | 9.3 s / 8.4 s wall time including container start; dbt's own time 4.47 s / 3.81 s |
| SC-003 data-test failures | 0: `PASS=75 WARN=0 ERROR=0 SKIP=0` on both builds |
| SC-004 second build identical | the md5 of all 7 models (ordered by key) is the same after both builds |
| Rows | `mart_team_form` 474,946 (2 x 237,473); both league marts 789; `mart_team_ratings` 1,221 teams, 783 rated |
| Spot check | E0 2015/16: 380 matches, shares 0.4132 / 0.2816 / 0.3053 |
| `public` after the builds | 237,473 matches, still at `0003_contract_constraints`, no new index |

Index and key measurements (FR-010, SC-005) from `scripts/explain_transforms.sql`: median of 5
`EXPLAIN (ANALYZE, BUFFERS)` runs per variant, "before" or candidate variants only inside
`BEGIN ... ROLLBACK`. The two rows marked ad hoc use the same method with a hand-written query
(the script covers the other marts); no variant left a catalog change.

| Index or key | Query | Before (ms) | After (ms) | Plan before -> after | Verdict |
|---|---|---:|---:|---|---|
| `mart_team_form (team_id, match_date, match_id)` | Q1: latest 10 matches of one team | 10.001 | 0.033 | Parallel Seq Scan + top-N sort, 7,323 buffers -> Index Scan Backward, 7 buffers | added (dbt `indexes` config), about 300 times faster |
| `mart_team_form primary key` | ad hoc: one `(match_id, team_id)` row | 0.030 | 0.025 | Bitmap Heap Scan -> Bitmap Heap Scan | kept for uniqueness, no speed effect |
| `mart_league_season_home_advantage primary key` | Q5: one league-season | 0.035 | 0.012 | Seq Scan, 11 buffers -> Index Scan, 3 buffers | kept (integrity; 789 rows) |
| `mart_league_season_goals primary key` | ad hoc: one league-season | 0.035 | 0.014 | Seq Scan, 10 buffers -> Index Scan, 3 buffers | kept (integrity; 789 rows) |
| `mart_team_ratings primary key` | Q6: one team's rating | 0.058 | 0.018 | Seq Scan, 18 buffers -> Index Scan, 6 buffers | kept (integrity; 1,221 rows) |
| `matches (match_date)` | Q2: the staging scan grouped by league-season, which every model repeats | 51.568 | 50.510 | Parallel Seq Scan -> Parallel Seq Scan | rejected: 2 % is noise; a full rebuild scans everything |
| `matches (match_date)` | Q3: cross-league date range | 3.240 | 0.057 | Index Only Scan on the natural key, 980 buffers -> Index Only Scan on the candidate, 3 buffers | rejected for now: no model or planned consumer runs this query |
| `matches (league_code, match_date)` | Q4: league plus date range | 0.082 | n/a | Index Only Scan on `matches_natural_key`, 5 buffers | rejected: the natural key already serves it |

Negative results are part of the answer: no index on `public.matches` helps the transform, so there
is no Alembic migration in this feature. Indexes on `public` change only through Alembic; to
re-measure, run the script, which never leaves anything behind.
