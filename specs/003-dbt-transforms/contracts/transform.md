# Contract: the transform command

**Feature**: `003-dbt-transforms` | Decisions: [research.md](../research.md) R3, R5, R15

The `football-stats` CLI (001/002 contracts) is unchanged. The transform is plain dbt, invoked in
one of two ways.

## Commands

| Where | Command |
|---|---|
| Compose stack (FR-008) | `docker compose run --rm transform` (image `football-stats-transform:local`, profile `transform`; build with `docker compose --profile transform build`) |
| Host (dev venv) | `uv run --frozen dbt build --project-dir transform --profiles-dir transform`, with the variables below exported (e.g. `set -a; . ./.env; set +a`) |

Recommended order: `docker compose run --rm ingest && docker compose run --rm transform`. Do not
run the transform while an ingest is running (research.md R16). The `transform` service depends on
`migrate` only; on a migrated but empty database it builds empty marts and exits 0.

`dbt build` runs, in dependency order, every model and every data test (generic and singular).
If a test fails, every model downstream of the failing node is skipped.

## Environment variables (all required, no defaults)

| Variable | Meaning | Compose value |
|---|---|---|
| `DBT_PG_HOST` | PostgreSQL host | `db` |
| `DBT_PG_PORT` | port (integer) | `5432` |
| `DBT_PG_USER` | user | `${POSTGRES_USER}` |
| `DBT_ENV_SECRET_PG_PASSWORD` | password (scrubbed from dbt logs) | `${POSTGRES_PASSWORD}` |
| `DBT_PG_DBNAME` | database | `${POSTGRES_DB}` |

Optional (dbt's own): `DBT_TARGET_PATH`, `DBT_LOG_PATH`. Defaults are `transform/target` and
`transform/logs` (git-ignored); the image uses `/tmp/dbt/target` and `/tmp/dbt/logs`.

`.env.example` gains the five variables for host use, derived from the existing ones
(`DBT_PG_HOST=127.0.0.1`, `DBT_PG_PORT=${POSTGRES_PORT}`, `DBT_PG_USER=${POSTGRES_USER}`,
`DBT_ENV_SECRET_PG_PASSWORD=${POSTGRES_PASSWORD}`, `DBT_PG_DBNAME=${POSTGRES_DB}`).

## Exit codes (dbt's, asserted by tests)

| Code | Meaning | Test |
|---|---|---|
| 0 | every model built and every test passed | `test_build_succeeds_and_runs_every_node`, smoke test |
| 1 | at least one model errored or test failed; stdout names it (`FAIL <n> <test_name>`), `run_results.json` has `status: "fail"` | `test_build_fails_and_names_failing_test` |
| 2 | dbt could not run: missing or invalid profile variable, database unreachable | `test_build_without_credentials_exits_2` |

## Outputs

- Schemas `analytics_staging` (views) and `analytics_marts` (tables): [marts.md](./marts.md). Both
  are fully replaced on every run. `public` is only read.
- Artifacts in `DBT_TARGET_PATH`: `run_results.json`, `manifest.json`, `compiled/`. Logs in
  `DBT_LOG_PATH` and on stdout.
- No network access other than PostgreSQL: anonymous usage tracking is off, and there are no dbt
  packages (research.md R6).
