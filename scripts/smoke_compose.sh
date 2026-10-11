#!/usr/bin/env bash
# Compose smoke test (US2): migrate twice with no schema change, then ingest a fixture
# through the container. Uses a throw-away project and a free host port; no dataset download.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
ENV_FILE="$(mktemp)"
# The fixture is copied to a temporary directory before it is bind-mounted: macOS asks for
# permission before Docker may read ~/Desktop or ~/Documents, which would hang an unattended run.
FIXTURE_DIR="$(mktemp -d)"
cp tests/fixtures/matches_valid.csv "${FIXTURE_DIR}/"
sed "s/^POSTGRES_PORT=.*/POSTGRES_PORT=${PORT}/" .env.example > "${ENV_FILE}"
COMPOSE=(docker compose -p football_smoke --env-file "${ENV_FILE}")

cleanup() {
  "${COMPOSE[@]}" --profile ingest down -v --remove-orphans >/dev/null 2>&1 || true
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

echo "smoke test passed"
