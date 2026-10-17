#!/usr/bin/env bash
# Compose smoke test: migrate twice with no schema change, ingest a fixture through the
# container, then run the dbt transform twice and check the mart row counts and the image
# separation. Uses a throw-away project and a free host port; no dataset download.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
ENV_FILE="$(mktemp)"
# The fixture is copied to a temporary directory before it is bind-mounted: macOS asks for
# permission before Docker may read ~/Desktop or ~/Documents, which would hang an unattended run.
FIXTURE_DIR="$(mktemp -d)"
cp tests/fixtures/matches_valid.csv "${FIXTURE_DIR}/"
# mktemp -d is 0700; the container runs as a different uid (10001), which must be able to read it.
chmod 755 "${FIXTURE_DIR}"
chmod 644 "${FIXTURE_DIR}/matches_valid.csv"
sed "s/^POSTGRES_PORT=.*/POSTGRES_PORT=${PORT}/" .env.example > "${ENV_FILE}"
COMPOSE=(docker compose -p football_smoke --env-file "${ENV_FILE}")

cleanup() {
  "${COMPOSE[@]}" --profile ingest --profile transform down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "${ENV_FILE}" "${FIXTURE_DIR}"
}
trap cleanup EXIT

set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a

structure() {
  "${COMPOSE[@]}" exec -T db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -At \
    -c '\dt' -c 'SELECT version_num FROM alembic_version'
}

echo "==> build the image (never test a stale one)"
"${COMPOSE[@]}" --profile ingest --profile transform build

echo "==> db up"
"${COMPOSE[@]}" up -d --wait db

echo "==> migrate (first run)"
"${COMPOSE[@]}" run --rm -T migrate
FIRST="$(structure)"
echo "${FIRST}"
for table in leagues teams matches ingest_runs excluded_rows alembic_version; do
  grep -q "public|${table}|table" <<<"${FIRST}" || { echo "missing table ${table}" >&2; exit 1; }
done

echo "==> migrate (second run must change nothing)"
"${COMPOSE[@]}" run --rm -T migrate
SECOND="$(structure)"
[ "${FIRST}" = "${SECOND}" ] || { echo "schema changed on second migrate" >&2; exit 1; }

echo "==> ingest fixture through the container"
REPORT="$("${COMPOSE[@]}" run --rm -T -v "${FIXTURE_DIR}:/fixtures:ro" ingest \
  football-stats ingest --source-file /fixtures/matches_valid.csv | tail -n 1)"
echo "${REPORT}"
python3 -c 'import json, sys; r = json.loads(sys.argv[1]); assert r["status"] == "succeeded" and r["rows_loaded"] == 12, r' "${REPORT}"

mart_counts() {
  "${COMPOSE[@]}" exec -T db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -At -c \
    "SELECT (SELECT count(*) FROM analytics_marts.mart_team_form) || '|' ||
            (SELECT count(*) FROM analytics_marts.mart_league_season_home_advantage) || '|' ||
            (SELECT count(*) FROM analytics_marts.mart_league_season_goals) || '|' ||
            (SELECT count(*) FROM analytics_marts.mart_team_ratings)"
}

# matches_valid.csv: 12 matches -> 24 team-form rows, 2 league-seasons, 6 teams.
EXPECTED_COUNTS="24|2|2|6"
for run in first second; do
  echo "==> transform (${run} run)"
  "${COMPOSE[@]}" run --rm -T transform
  COUNTS="$(mart_counts)"
  echo "mart counts: ${COUNTS}"
  [ "${COUNTS}" = "${EXPECTED_COUNTS}" ] || { echo "unexpected mart counts (want ${EXPECTED_COUNTS})" >&2; exit 1; }
done

echo "==> image separation: no dbt in the app image, no football_stats in the transform image"
if docker run --rm --entrypoint python football-stats:local -c "import dbt" >/dev/null 2>&1; then
  echo "dbt is importable in the app image" >&2; exit 1
fi
if docker run --rm --entrypoint python football-stats-transform:local -c "import football_stats" >/dev/null 2>&1; then
  echo "football_stats is importable in the transform image" >&2; exit 1
fi

echo "smoke test passed"
