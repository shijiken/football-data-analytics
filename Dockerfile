# Three stages sharing one uv.lock: `base` (tooling, user), `transform` (dbt only) and `app`
# (the ingestion CLI; last, so a plain `docker build .` and the `build: .` services build it).
FROM python:3.12.11-slim-bookworm AS base

ENV UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir uv==0.12.5 \
    && useradd --create-home --uid 10001 app

WORKDIR /app

# Dependencies first so later layers are cached until pyproject.toml / uv.lock change.
COPY pyproject.toml uv.lock ./

FROM base AS transform

# Only the dbt group: no project code, no polars, no pandera (research.md R2).
RUN uv sync --frozen --only-group transform

COPY transform/ transform/

ENV PATH="/app/.venv/bin:$PATH" \
    DBT_TARGET_PATH=/tmp/dbt/target \
    DBT_LOG_PATH=/tmp/dbt/logs \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false

USER app
WORKDIR /app/transform

CMD ["dbt", "build", "--project-dir", "/app/transform", "--profiles-dir", "/app/transform"]

FROM base AS app

RUN uv sync --frozen --no-default-groups --no-install-project

COPY src/ src/
COPY migrations/ migrations/
COPY alembic.ini ./
RUN uv sync --frozen --no-default-groups

ENV PATH="/app/.venv/bin:$PATH" \
    RAW_DATA_DIR=/app/data/raw

# The raw-data directory is created here so the `rawdata` named volume is initialised writable
# by the non-root user.
RUN mkdir -p /app/data/raw \
    && chown -R app:app /app/data
USER app

CMD ["football-stats", "ingest"]
