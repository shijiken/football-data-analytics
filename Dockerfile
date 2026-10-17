FROM python:3.12.11-slim-bookworm

ENV UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir uv==0.12.5

WORKDIR /app

# Dependencies first so this layer is cached until pyproject.toml / uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
COPY migrations/ migrations/
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    RAW_DATA_DIR=/app/data/raw

# Non-root user; the raw-data directory is created here so the `rawdata` named volume is
# initialised writable by it.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data/raw \
    && chown -R app:app /app/data
USER app

CMD ["football-stats", "ingest"]
