# Quickstart & Validation Guide: Match Data Ingestion

**Feature**: `001-match-data-ingestion` | Contracts: [contracts/cli.md](./contracts/cli.md) |
Schema: [data-model.md](./data-model.md)

This guide is the manual acceptance run. Everything that can be automated is in the quality gate
(`scripts/check.sh`); steps marked **(manual)** need the network or a stopwatch and are run once per
release, with results written to the README.

## Prerequisites

- Docker 24+ with Compose v2 (daemon running)
- For local development and the gate: Python 3.12, uv 0.12.5
- Network access to `raw.githubusercontent.com` for the real-data run only

## 0. Quality gate (automated, no network for data)

```bash
uv sync --frozen
scripts/check.sh
```

Expected: steps `ruff format --check`, `ruff check`, `mypy`, unit tests, integration tests run in
that order; exit code 0. Any failure stops the script with a non-zero exit.

## 1. Fresh checkout → loaded database (SC-001, US1-1) **(manual, timed)**

```bash
cp .env.example .env
time docker compose run --rm ingest
```

Expected:
- stdout is a JSON report with `"status": "succeeded"`;
- on the pinned source (research.md R1): `rows_read` 238859, `rows_loaded` 238854,
  `rows_excluded` 5, `excluded_by_reason.missing_result` 4, `excluded_by_reason.missing_field` 1,
  all other reasons 0, `matches_inserted` 238854; the database then holds 38 leagues and 1221 teams;
- total wall time < 10 minutes including image build and download (SC-001). Record it.

## 2. Timed full ingestion (SC-002) **(manual)**

With images already built (step 1), on an empty database:

```bash
docker compose down -v && docker compose up -d
time docker compose run --rm ingest
```

`down -v` also removes the `rawdata` volume, so this measures download + clean + load. Expected:
`real` < 5 minutes. Record the measured value in the README "Measured numbers" section.

## 3. Structure from scratch and re-apply (US2)

```bash
docker compose down -v
docker compose up -d            # db becomes healthy, migrate runs once (US2-1, US2-2)
docker compose run --rm migrate # second application (US2-3)
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c '\dt'
```

Expected: tables `leagues`, `teams`, `matches`, `ingest_runs`, `excluded_rows`, `alembic_version`;
the second `migrate` exits 0 and prints no new revision.

(`POSTGRES_USER`/`POSTGRES_DB` are in `.env`; `set -a; . ./.env; set +a` loads them into the shell.)

## 4. Query by league and season (US1-2)

Seasons are date ranges in this feature (research.md R15):

```sql
SELECT m.match_date, h.name AS home, a.name AS away, m.ft_home_goals, m.ft_away_goals
FROM matches m
JOIN teams h ON h.team_id = m.home_team_id
JOIN teams a ON a.team_id = m.away_team_id
WHERE m.league_code = 'E0' AND m.match_date BETWEEN '2015-07-01' AND '2016-06-30'
ORDER BY m.match_date, home;
```

Expected: 380 rows (a 20-team Premier League season), each with date, both teams and score.

## 5. Team spelling variants collapse (US1-3, SC-006)

```sql
SELECT name, team_key FROM teams WHERE team_key IN ('ajax', 'colon santa fe', 'kaiserslautern');
SELECT lower(regexp_replace(btrim(name), '\s+', ' ', 'g')) k, count(*) FROM teams GROUP BY k HAVING count(*) > 1;
```

Expected: one row per key in the first query; zero rows in the second.

## 6. Excluded rows are queryable (US3-3, FR-013)

```sql
SELECT reason, detail, source_row_number, raw_record->>'HomeTeam' AS home, run_id
FROM excluded_rows ORDER BY source_row_number;
```

Expected: 5 rows — four `missing_result` (e.g. G1 2015-05-10 Niki Volos v OFI) and one
`missing_field` (the empty record) — each with its original content and run id.

## 7. Accounting reconciles (SC-003)

```sql
SELECT r.run_id, r.rows_read,
       (SELECT count(*) FROM matches m WHERE m.source_sha256 = r.source_sha256)       AS loaded_in_db,
       (SELECT count(*) FROM excluded_rows e WHERE e.source_sha256 = r.source_sha256) AS excluded_in_db
FROM ingest_runs r ORDER BY r.run_id DESC LIMIT 1;
```

Expected: `rows_read = loaded_in_db + excluded_in_db`.

## 8. Idempotent re-run (SC-005, US3-4, FR-011)

```bash
docker compose run --rm ingest
```

Expected: same `rows_*` and `excluded_by_reason` as step 1; `matches_inserted` 0,
`matches_updated` 0, `matches_unchanged` 238854; table counts unchanged:

```sql
SELECT (SELECT count(*) FROM leagues) l, (SELECT count(*) FROM teams) t,
       (SELECT count(*) FROM matches) m, (SELECT count(*) FROM excluded_rows) e;
```

## 9. Spot check against the source (SC-004) **(manual)**

```bash
docker compose run --rm ingest football-stats verify --sample 20 --seed 42
```

Expected: `{"status": "succeeded", "checked": 20, "differences": []}`, exit 0.
`--sample 0` checks every row.

## 10. Failure leaves the database unchanged (FR-012, edge cases)

```bash
# Unreachable source
docker compose run --rm -e SOURCE_URL=http://127.0.0.1:9/none.csv ingest
# Wrong columns
docker compose run --rm -v "$PWD/tests/fixtures:/fixtures:ro" ingest \
  football-stats ingest --source-file /fixtures/matches_missing_columns.csv
```

Expected: exit 3 and exit 4 respectively, a `"status": "failed"` report naming the problem (the
second names `AwayTeam` and `FTHome`), and the table counts from step 8 unchanged.

## 11. Local development without the app container

```bash
docker compose up -d db
set -a; . ./.env; set +a
export DATABASE_URL="postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@127.0.0.1:${POSTGRES_PORT:-5432}/$POSTGRES_DB"
uv run alembic upgrade head
uv run football-stats ingest --source-file tests/fixtures/matches_valid.csv
```
