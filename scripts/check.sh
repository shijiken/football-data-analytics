#!/usr/bin/env bash
# Deterministic quality gate: format, lint, types, unit tests, integration tests.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> ruff format --check"
uv run --frozen ruff format --no-cache --check .
echo "==> ruff check"
uv run --frozen ruff check --no-cache .
echo "==> mypy"
uv run --frozen mypy src tests
echo "==> unit tests"
uv run --frozen pytest tests/unit --randomly-seed=20261011 --disable-socket
echo "==> integration tests"
uv run --frozen pytest tests/integration --randomly-seed=20261011
