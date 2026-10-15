# Quickstart & Validation Guide: Match Data Contract

**Feature**: `002-data-contract` | Contracts: [data-contract.md](./contracts/data-contract.md),
[cli.md](./contracts/cli.md) | Schema changes: [data-model.md](./data-model.md)

Everything that can be automated is in the quality gate (`scripts/check.sh`). Steps marked
**(manual)** need the real dataset (network) or a stopwatch; they are run once by task T036 and their
results are written to the README and `decisions.md`. Base setup (`.env`, Compose) is unchanged from
[001 quickstart](../001-match-data-ingestion/quickstart.md).

Expected numbers below are the planning profile (research.md R16). If a measured value differs,
**stop and report the difference**; do not edit the expectation to match.

## 0. Quality gate (automated)

```bash
uv sync --frozen
scripts/check.sh
scripts/smoke_compose.sh
```

Expected: exit 0 for both. The gate covers every FR/SC except the manual items below (plan.md
"FR / SC → Test Mapping").

## 1. Upgrade an existing feature-001 database (FR-008, US2-2, US3) **(manual)**

Start from the database left by feature 001 (238,854 matches, 5 excluded rows, revision
`0001_initial_schema`). If it no longer exists, build it first by checking out the commit before this
feature and running `docker compose run --rm ingest` once.

```bash
docker compose build
docker compose run --rm migrate          # applies 0002 and 0003
docker compose run --rm migrate          # second time: no-op (FR-008)
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT version_num FROM alembic_version;
   SELECT conname, convalidated FROM pg_constraint WHERE conname LIKE 'matches_contract_%' ORDER BY 1;
   SELECT run_id, contract_version, violations_by_rule FROM ingest_runs ORDER BY run_id;"
```

Expected: both migrate runs exit 0; version `0003_contract_constraints`; nine `matches_contract_*`
constraints, `convalidated = f`; existing runs show `contract_version 0` and `{}`. Matches are still
238,854 (nothing deleted by the migration).

## 2. Converging re-run (FR-009, US3-1, SC-001) **(manual, timed)**

```bash
time docker compose run --rm ingest
```

Expected report: `contract_version` 1; `rows_read` 238859; `rows_loaded` 237473; `rows_excluded`
1386; `excluded_by_reason`: `missing_field` 1, `missing_result` 4, `contract_violation` 1381, others
0; `violations_by_rule`: `sot_le_shots_home` 7, `sot_le_shots_away` 13, `odds_min` 8,
`max_odds_ge_avg` 1357, others 0; `matches_inserted` 0, `matches_updated` 0, `matches_unchanged`
237473, `matches_removed` 1381, `excluded_rows_removed` 0. Then SC-001 on the whole table. This is a **read-only proof**: the transaction is
rolled back, so the constraints stay `NOT VALID`. Never run `VALIDATE CONSTRAINT` outside
`BEGIN … ROLLBACK`; a committed validation is a schema change, and schema changes go through
Alembic revisions only (research.md R8):

```bash
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
BEGIN;
ALTER TABLE matches VALIDATE CONSTRAINT matches_contract_ht_le_ft_home;
ALTER TABLE matches VALIDATE CONSTRAINT matches_contract_ht_le_ft_away;
ALTER TABLE matches VALIDATE CONSTRAINT matches_contract_ft_result_consistent;
ALTER TABLE matches VALIDATE CONSTRAINT matches_contract_ht_result_consistent;
ALTER TABLE matches VALIDATE CONSTRAINT matches_contract_sot_le_shots_home;
ALTER TABLE matches VALIDATE CONSTRAINT matches_contract_sot_le_shots_away;
ALTER TABLE matches VALIDATE CONSTRAINT matches_contract_odds_min;
ALTER TABLE matches VALIDATE CONSTRAINT matches_contract_max_odds_ge_avg;
ALTER TABLE matches VALIDATE CONSTRAINT matches_contract_date_in_range;
ROLLBACK;
SQL
```

Expected: every statement succeeds (0 loaded matches violate any rule), exit 0.

## 3. Fresh full run, timed (SC-005, SC-006, FR-011, FR-012) **(manual, timed)**

```bash
docker compose down -v && docker compose up -d
time docker compose run --rm ingest
```

Expected: same `rows_*`, `excluded_by_reason` and `violations_by_rule` as step 2;
`matches_inserted` 237473; `matches_removed` 0; leagues 38, teams 1221; `real` < 5 minutes.
Record in the README: the per-rule table (count and share of rows read), run date, contract
version, wall time, `duration_seconds`, machine. Record in `decisions.md` any rule above 1 % of rows
read (review outcome: keep / adjust / drop, with reasoning) — the profile predicts none; the
largest, `max_odds_ge_avg` (0.57 %), gets an observation entry either way.

## 4. Re-run is a no-op (US3-3)

```bash
docker compose run --rm ingest
```

Expected: identical counts to step 3 except `matches_inserted` 0, `matches_unchanged` 237473,
`matches_removed` 0, `excluded_rows_removed` 0.

## 5. Inspect violations (US1, US4-1)

```sql
SELECT detail, count(*) FROM excluded_rows WHERE reason = 'contract_violation'
GROUP BY detail ORDER BY count(*) DESC;
SELECT run_id, contract_version, violations_by_rule->>'max_odds_ge_avg' AS max_odds
FROM ingest_runs ORDER BY run_id DESC LIMIT 1;
```

Expected (profile): `max_odds_ge_avg` 1356, `sot_le_shots_away` 10, `odds_min` 7,
`sot_le_shots_home` 4, `sot_le_shots_home, sot_le_shots_away` 3, `odds_min, max_odds_ge_avg` 1;
the last run shows version 1 and 1357.

## 6. Database refuses violating writes (US2-1, SC-003)

```sql
BEGIN;
UPDATE matches SET ht_home_goals = ft_home_goals + 1
WHERE match_id = (SELECT min(match_id) FROM matches WHERE ft_home_goals IS NOT NULL);
ROLLBACK;
```

Expected: `ERROR: new row for relation "matches" violates check constraint
"matches_contract_ht_le_ft_home"`. (Every rule is covered automatically by
`test_db_agrees_with_pipeline_on_contract_case`.)

## 7. Spot check still passes (001 SC-004)

```bash
docker compose run --rm ingest football-stats verify --sample 20 --seed 42
```

Expected: `{"status": "succeeded", "checked": 20, "differences": []}`.
