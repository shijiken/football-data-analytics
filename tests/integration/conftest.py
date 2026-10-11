"""Shared integration fixtures: PostgreSQL container, per-test databases, loopback HTTP."""

from __future__ import annotations

import itertools
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql
from testcontainers.postgres import PostgresContainer

from football_stats.config import Settings
from tests.integration import helpers

_counter = itertools.count(1)


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{database}"))


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Start one PostgreSQL container for the whole test session."""
    with PostgresContainer("postgres:17.6-alpine", driver=None) as container:
        yield container


@pytest.fixture(scope="session")
def admin_url(postgres_container: PostgresContainer) -> str:
    """Return a libpq URL to the maintenance ``postgres`` database."""
    return _with_database(postgres_container.get_connection_url(), "postgres")


def _create_database(admin_url: str, name: str, template: str | None = None) -> str:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        query = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
        if template is not None:
            query += sql.SQL(" TEMPLATE {}").format(sql.Identifier(template))
        conn.execute(query)
    return _with_database(admin_url, name)


def _drop_database(admin_url: str, name: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


@pytest.fixture
def empty_db_url(admin_url: str) -> Iterator[str]:
    """Create an empty database for one test and drop it afterwards."""
    name = f"test_{next(_counter)}"
    url = _create_database(admin_url, name)
    try:
        yield url
    finally:
        _drop_database(admin_url, name)


Route = tuple[int, dict[str, str], bytes, int | None]


@dataclass
class LoopbackServer:
    """Handle on the in-process HTTP server used by download tests."""

    base_url: str
    routes: dict[str, Route] = field(default_factory=dict)


@pytest.fixture
def loopback_http_server() -> Iterator[LoopbackServer]:
    """Serve ``routes`` (path -> status, headers, body, truncate_after) on 127.0.0.1."""
    routes: dict[str, Route] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, headers, body, truncate_after = routes.get(
                self.path, (404, {}, b"not found", None)
            )
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            if "Content-Length" not in headers:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            payload = body if truncate_after is None else body[:truncate_after]
            self.wfile.write(payload)
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, format: str, *args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield LoopbackServer(base_url=f"http://127.0.0.1:{server.server_address[1]}", routes=routes)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def template_db(admin_url: str) -> Iterator[str]:
    """Create a migrated template database once per session and return its name."""
    name = "football_template"
    url = _create_database(admin_url, name)
    helpers.upgrade(url)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("ALTER DATABASE {} IS_TEMPLATE true").format(sql.Identifier(name)))
    yield name
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("ALTER DATABASE {} IS_TEMPLATE false").format(sql.Identifier(name)))
    _drop_database(admin_url, name)


@pytest.fixture
def db_url(admin_url: str, template_db: str) -> Iterator[str]:
    """Create a migrated database cloned from the template for one test."""
    name = f"test_{next(_counter)}"
    url = _create_database(admin_url, name, template=template_db)
    try:
        yield url
    finally:
        _drop_database(admin_url, name)


@pytest.fixture
def db_conn(db_url: str) -> Iterator[psycopg.Connection]:
    """Open a transactional (non-autocommit) connection to the per-test database."""
    with psycopg.connect(db_url) as conn:
        yield conn


@pytest.fixture
def settings(db_url: str, tmp_path: Path) -> Settings:
    """Settings for pipeline tests: raw files go to ``tmp_path``, never the repository."""
    return Settings(
        database_url=db_url,
        source_url="file:///nonexistent/override-in-test.csv",
        raw_data_dir=tmp_path / "raw",
        log_level="DEBUG",
    )
